"""命令解析、图片处理与描述构建测试。"""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable, Iterator, cast, get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, PngImagePlugin

from src.core.managers.media_manager import MediaManager

from image_generator_plugin_neo.commands import parsing
from image_generator_plugin_neo.config import (
    ImageGeneratorConfig,
    PromptPresetConfig,
    VibeItemConfig,
)
from image_generator_plugin_neo.actions.draw import DrawAction
from image_generator_plugin_neo.actions.base import BaseImageAction
from image_generator_plugin_neo.actions.director import BaseDirectorAction
from image_generator_plugin_neo.actions.edit import EditImageAction
from image_generator_plugin_neo.actions.enhance import EnhanceAction
from image_generator_plugin_neo.actions.inpaint import InpaintAction
from image_generator_plugin_neo.actions.upscale import UpscaleAction
from image_generator_plugin_neo.descriptions import build_draw_description
from image_generator_plugin_neo.engine import assets
from image_generator_plugin_neo.engine.settings import EngineSettings
from image_generator_plugin_neo.generated_image_context import GeneratedImageContextHandler
from image_generator_plugin_neo.media import extract_image_by_media_id, image_ops
from image_generator_plugin_neo.plugin import ImageGeneratorPlugin
from image_generator_plugin_neo import media
from src.app.plugin_system.types import EventType, Image as LLMImage, LLMPayload, ROLE, Text
from src.kernel.event import EventDecision


def encode_png(image: Image.Image) -> str:
    """把 Pillow 图片编码为 base64 PNG。"""

    import base64

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def clean_pending_images() -> Iterator[dict[str, list[str]]]:
    """隔离生成图待注入列表。"""
    media.pending_generated_images.clear()
    yield media.pending_generated_images
    media.pending_generated_images.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "inject_into_context", "expected_context_mode"),
    [
        ("vlm", False, "placeholder"),
        ("base64", False, "placeholder"),
        ("vlm", True, "description"),
        ("base64", True, "native"),
    ],
)
async def test_send_generated_image_uses_media_context_mode(
    mode: str, inject_into_context: bool, expected_context_mode: str,
    clean_pending_images: dict[str, list[str]],
) -> None:
    """关闭注入时只保留占位符，开启后按所选模式发送。"""
    with patch.object(media, "send_media", new_callable=AsyncMock, return_value=True) as send:
        assert await media.send_generated_image(
            "image-data", "stream", mode, inject_into_context=inject_into_context,
        )
    send.assert_awaited_once_with(
        "image", "image-data", stream_id="stream", reply_to=None,
        context_mode=expected_context_mode,
    )
    expected_ids = (
        [hashlib.sha256(b"image-data").hexdigest()]
        if expected_context_mode == "native" else []
    )
    assert clean_pending_images.get("stream", []) == expected_ids


@pytest.mark.asyncio
async def test_generated_image_context_injects_once_after_success(
    clean_pending_images: dict[str, list[str]],
) -> None:
    """同流的聊天请求注入原图，成功后下轮不重复注入。"""
    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.plugin.inject_generated_image = True
    config.plugin.output_image_context = "base64"
    handler = GeneratedImageContextHandler(ImageGeneratorPlugin(config))
    media_id = "a" * 64
    clean_pending_images["stream-a"] = [media_id]
    metadata = {"stream_id": "stream-a"}
    params = {
        "meta_data": metadata,
        "payloads": [LLMPayload(ROLE.USER, Text("生成一张图"))],
        "tools": [DrawAction],
    }

    with patch(
        "image_generator_plugin_neo.generated_image_context.get_media_file",
        new_callable=AsyncMock, return_value="base64|aGVsbG8=",
    ) as load:
        decision, updated = await handler.execute(EventType.BEFORE_LLM_REQUEST, params)

    assert decision == EventDecision.SUCCESS
    injected = updated["payloads"][-1]
    assert injected.role == ROLE.USER
    assert [part.value for part in injected.content if isinstance(part, LLMImage)] == ["aGVsbG8="]
    assert media_id in "".join(part.text for part in injected.content if isinstance(part, Text))
    load.assert_awaited_once_with(media_id)

    await handler.execute(EventType.AFTER_LLM_REQUEST, {"meta_data": metadata, "success": True})
    assert "stream-a" not in clean_pending_images
    next_params = {"meta_data": metadata, "payloads": updated["payloads"], "tools": [DrawAction]}
    decision, _ = await handler.execute(EventType.BEFORE_LLM_REQUEST, next_params)
    assert decision == EventDecision.PASS
    assert len(next_params["payloads"]) == 2


@pytest.mark.asyncio
async def test_generated_image_context_skips_unrelated_requests(
    clean_pending_images: dict[str, list[str]],
) -> None:
    """不将待注入图像送给其他流或同流的无关模型调用。"""
    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.plugin.inject_generated_image = True
    config.plugin.output_image_context = "base64"
    handler = GeneratedImageContextHandler(ImageGeneratorPlugin(config))
    media_id = "b" * 64
    clean_pending_images["stream-a"] = [media_id]
    payloads = [LLMPayload(ROLE.USER, Text("无关请求"))]
    for stream_id, tools in (("stream-b", [DrawAction]), ("stream-a", [])):
        decision, _ = await handler.execute(EventType.BEFORE_LLM_REQUEST, {
            "meta_data": {"stream_id": stream_id}, "payloads": payloads, "tools": tools,
        })
        assert decision == EventDecision.PASS

    with patch(
        "image_generator_plugin_neo.generated_image_context.get_media_file",
        new_callable=AsyncMock, return_value="aGVsbG8=",
    ):
        decision, _ = await handler.execute(EventType.BEFORE_LLM_REQUEST, {
            "meta_data": {"stream_id": "stream-a"},
            "payloads": [LLMPayload(ROLE.USER, Text(f"Bot: [图片({media_id})]"))],
            "tools": [],
        })
    assert decision == EventDecision.SUCCESS


@pytest.mark.asyncio
async def test_generated_image_context_retry_keeps_pending(
    clean_pending_images: dict[str, list[str]],
) -> None:
    """未成功的模型请求不消费图片，下次发送仍可注入。"""
    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.plugin.inject_generated_image = True
    config.plugin.output_image_context = "base64"
    handler = GeneratedImageContextHandler(ImageGeneratorPlugin(config))
    media_id = "c" * 64
    clean_pending_images["stream-a"] = [media_id]
    metadata = {"stream_id": "stream-a"}
    with patch(
        "image_generator_plugin_neo.generated_image_context.get_media_file",
        new_callable=AsyncMock, return_value="aGVsbG8=",
    ) as load:
        for _ in range(2):
            params = {"meta_data": metadata, "payloads": [LLMPayload(ROLE.USER, Text("续轮"))], "tools": [DrawAction]}
            decision, _ = await handler.execute(EventType.BEFORE_LLM_REQUEST, params)
            assert decision == EventDecision.SUCCESS
            await handler.execute(EventType.AFTER_LLM_REQUEST, {"meta_data": metadata, "success": False})
    assert load.await_count == 2
    assert clean_pending_images["stream-a"] == [media_id]


@pytest.mark.asyncio
async def test_generated_image_context_preserves_order_and_drops_missing_media(
    clean_pending_images: dict[str, list[str]],
) -> None:
    """批量生图按发送顺序注入，无法回查的媒体不阻塞后续请求。"""
    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.plugin.inject_generated_image = True
    config.plugin.output_image_context = "base64"
    handler = GeneratedImageContextHandler(ImageGeneratorPlugin(config))
    first_id, missing_id, last_id = "a" * 64, "b" * 64, "c" * 64
    clean_pending_images["stream-a"] = [first_id, missing_id, last_id]
    metadata = {"stream_id": "stream-a"}
    with patch(
        "image_generator_plugin_neo.generated_image_context.get_media_file",
        new_callable=AsyncMock, side_effect=["YWJj", None, "ZGVm"],
    ) as load:
        _, params = await handler.execute(EventType.BEFORE_LLM_REQUEST, {
            "meta_data": metadata,
            "payloads": [LLMPayload(ROLE.USER, Text("继续"))],
            "tools": [DrawAction],
        })
    assert [part.value for part in params["payloads"][-1].content if isinstance(part, LLMImage)] == [
        "YWJj", "ZGVm",
    ]
    assert [call.args[0] for call in load.await_args_list] == [first_id, missing_id, last_id]
    assert clean_pending_images["stream-a"] == [first_id, last_id]
    await handler.execute(EventType.AFTER_LLM_REQUEST, {"meta_data": metadata, "success": True})
    assert "stream-a" not in clean_pending_images


@pytest.mark.asyncio
async def test_generated_image_context_cleared_when_mode_disabled(
    clean_pending_images: dict[str, list[str]],
) -> None:
    """关闭原图注入后不把之前未消费的图片送进下一次模型请求。"""
    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.plugin.inject_generated_image = True
    config.plugin.output_image_context = "base64"
    plugin = ImageGeneratorPlugin(config)
    clean_pending_images["stream-a"] = ["a" * 64]
    new_config = ImageGeneratorConfig()
    new_config.plugin.enabled = True
    new_config.plugin.output_image_context = "vlm"
    with patch.object(plugin, "_sync_rule_reminder"):
        await plugin.apply_config(new_config)
    assert not clean_pending_images


@pytest.mark.asyncio
async def test_generated_image_vlm_error_falls_back_to_placeholder() -> None:
    """框架识别抛出异常时，图片仅以占位符发送一次。"""
    with patch.object(
        media, "send_media", new_callable=AsyncMock,
        side_effect=[RuntimeError("VLM unavailable"), True],
    ) as send:
        assert await media.send_generated_image(
            "image-data", "stream", "vlm", inject_into_context=True,
        )
    assert [call.kwargs["context_mode"] for call in send.await_args_list] == [
        "description", "placeholder",
    ]


@pytest.mark.asyncio
async def test_generated_image_failed_send_is_not_retried() -> None:
    """平台发图失败不当作识别错误重复发送。"""
    with patch.object(media, "send_media", new_callable=AsyncMock, return_value=False) as send:
        assert not await media.send_generated_image(
            "image-data", "stream", "vlm", inject_into_context=True,
        )
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_action_sends_using_configured_context_mode(tmp_path: Path) -> None:
    """Action 入口透传插件模式，发图失败不会报告成功。"""
    config = ImageGeneratorConfig()
    config.plugin.output_image_context = "base64"
    config.plugin.inject_generated_image = True
    action = cast(BaseImageAction, SimpleNamespace(
        plugin_config=config, chat_stream=SimpleNamespace(stream_id="stream"),
    ))
    with patch("image_generator_plugin_neo.actions.base.storage.read_image_base64", return_value="aGVsbG8="), \
         patch("image_generator_plugin_neo.actions.base.send_generated_image", new_callable=AsyncMock, return_value=False) as send:
        result = await BaseImageAction._send_image(action, tmp_path / "generated.png")

    assert result == (False, "图片发送失败")
    send.assert_awaited_once_with(
        "aGVsbG8=", stream_id="stream", mode="base64", inject_into_context=True,
    )


@pytest.mark.asyncio
async def test_action_returns_cached_media_id(tmp_path: Path) -> None:
    """Action 返回发送内容的媒体 ID，并确认图片可在媒体库回查。"""
    config = ImageGeneratorConfig()
    action = cast(BaseImageAction, SimpleNamespace(
        plugin_config=config, chat_stream=SimpleNamespace(stream_id="stream"),
    ))
    image_b64 = "aGVsbG8="
    media_id = hashlib.sha256(image_b64.encode("utf-8")).hexdigest()
    with patch("image_generator_plugin_neo.actions.base.storage.read_image_base64", return_value=image_b64), \
         patch("image_generator_plugin_neo.actions.base.send_generated_image", new_callable=AsyncMock, return_value=True), \
         patch("image_generator_plugin_neo.actions.base.get_media_info", new_callable=AsyncMock, return_value={"image_id": media_id, "path": "cached.png"}) as get_info:
        result = await BaseImageAction._send_image(action, tmp_path / "generated.png")

    assert result == (True, media_id)
    get_info.assert_awaited_once_with(media_id)


@pytest.mark.asyncio
async def test_action_rejects_uncached_sent_image(tmp_path: Path) -> None:
    """平台发图成功但缓存缺失时，不返回无法使用的媒体 ID。"""
    config = ImageGeneratorConfig()
    action = cast(BaseImageAction, SimpleNamespace(
        plugin_config=config, chat_stream=SimpleNamespace(stream_id="stream"),
    ))
    with patch("image_generator_plugin_neo.actions.base.storage.read_image_base64", return_value="aGVsbG8="), \
         patch("image_generator_plugin_neo.actions.base.send_generated_image", new_callable=AsyncMock, return_value=True), \
         patch("image_generator_plugin_neo.actions.base.get_media_info", new_callable=AsyncMock, return_value=None):
        result = await BaseImageAction._send_image(action, tmp_path / "generated.png")

    assert result[0] is False
    assert "缓存不可回查" in result[1]


@pytest.mark.asyncio
async def test_action_returns_all_media_ids_without_renaming(tmp_path: Path) -> None:
    """多张图片逐张发送并返回对应 ID，保持引擎生成的本地文件名。"""
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    send_image = AsyncMock(side_effect=[(True, "first-id"), (True, "second-id")])
    action = cast(BaseImageAction, SimpleNamespace(
        image_plugin=object(), chat_stream=SimpleNamespace(stream_id="stream"),
        _send_image=send_image,
    ))

    async def run_work(
        plugin: object,
        factory: Callable[[], Awaitable[tuple[bool, str]]],
        **options: object,
    ) -> tuple[bool, str]:
        return await factory()

    work = AsyncMock(return_value=(
        SimpleNamespace(success=True, path=first),
        SimpleNamespace(success=True, path=second),
    ))
    with patch("image_generator_plugin_neo.actions.base.background.run_shielded", new=run_work):
        success, message = await BaseImageAction.run_in_background(
            action, work, task_name="draw", purpose="draw", success_message="已发送", error_prefix="失败",
        )

    assert success is True
    assert message == "已发送（media_id: first-id, second-id）"
    assert "first.png" not in message
    assert send_image.await_args_list[0].args == (first,)
    assert send_image.await_args_list[1].args == (second,)


def test_action_schemas_expose_media_ids_not_filenames() -> None:
    """所有生图 Action 均不要求模型传入输出或来源文件名。"""
    for action in (
        DrawAction, EditImageAction, InpaintAction, BaseDirectorAction,
        EnhanceAction, UpscaleAction,
    ):
        parameters = inspect.signature(action.execute).parameters
        assert "output_filename" not in parameters
        assert "image_filename" not in parameters


def test_action_media_id_matches_framework_hash() -> None:
    """Action 使用的纯 base64 图片 ID 与框架发送缓存算法相同。"""
    image_b64 = encode_png(Image.new("RGB", (2, 2), (10, 20, 30)))
    assert hashlib.sha256(image_b64.encode("utf-8")).hexdigest() == MediaManager.compute_media_hash(
        f"base64|{image_b64}"
    )


def test_extract_scale_flags_keeps_zero_value() -> None:
    """验证 --rescale 0 不会被当成未设置。"""

    flags = parsing.extract_scale_flags("1girl --scale 7 --rescale 0")
    assert flags.scale == 7.0
    assert flags.cfg_rescale == 0.0
    assert flags.remainder == "1girl"


def test_extract_generation_flags_removes_only_explicit_options() -> None:
    """验证命令模型与生成策略参数会被提取且不污染提示词。"""

    flags = parsing.extract_generation_flags(
        '1girl, holding sign "Hello" --model nai-diffusion-5-full '
        "--steps 24 --seed 0 --count 3 --variety-plus true --render-text"
    )
    assert flags.model == "nai-diffusion-5-full"
    assert flags.steps == 24
    assert flags.seed == 0
    assert flags.count == 3
    assert flags.variety_plus is True
    assert flags.render_text is True
    assert flags.remainder == '1girl, holding sign "Hello"'


@pytest.mark.parametrize("flag", ["--seed -1", "--count 0", "--count 5"])
def test_extract_generation_flags_rejects_invalid_seed_or_count(flag: str) -> None:
    """验证命令明确拒绝非法 seed 与图片数量。"""

    with pytest.raises(ValueError):
        parsing.extract_generation_flags(f"1girl {flag}")


def test_split_prompt_supports_fullwidth_colon() -> None:
    """验证正负面切分兼容全角冒号。"""

    assert parsing.split_prompt("正面：1girl 负面：chibi") == ("1girl", "chibi")
    assert parsing.split_prompt("1girl, pink hair") == ("1girl, pink hair", None)


def test_parse_size_token_handles_aliases_and_literals() -> None:
    """验证画幅词元同时支持中文别名与显式尺寸。"""

    assert parsing.parse_size_token("竖图") == (832, 1216)
    assert parsing.parse_size_token("1216×832") == (1216, 832)
    assert parsing.parse_size_token("1girl") is None


def test_parse_edit_args_rejects_strength_outside_range() -> None:
    """验证位置强度参数超出范围时明确报错。"""

    assert parsing.parse_edit_args(["1girl", "0.5"]) == ("1girl", 0.5)
    with pytest.raises(ValueError, match="strength"):
        parsing.parse_edit_args(["1girl", "5"])
    assert parsing.parse_edit_args([]) == (parsing.DEFAULT_EDIT_PROMPT, None)


def test_extract_reference_flags_rejects_values_outside_range() -> None:
    """验证参考参数拒绝范围外数值。"""

    with pytest.raises(ValueError, match="fidelity"):
        parsing.extract_reference_flags(
            "1girl --参考类型 风格 --fidelity 2.0 --strength -1"
        )


def test_strip_metadata_clears_alpha_lsb_and_removes_text(tmp_path: Path) -> None:
    """验证元数据剥离：alpha & 0xFE 清零 LSB + 移除 tEXt/iTXt 文本块。"""

    source = tmp_path / "source.png"
    image = Image.new("RGBA", (4, 1))
    image.putdata(
        [(255, 0, 0, 0), (255, 0, 0, 1), (255, 0, 0, 128), (255, 0, 0, 255)]
    )
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "hidden prompt")
    image.save(source, pnginfo=metadata)

    stripped = image_ops.strip_png_metadata(source.read_bytes())
    with Image.open(io.BytesIO(stripped)) as output:
        alpha_channel = output.getchannel("A")
        alpha = [alpha_channel.getpixel((x, 0)) for x in range(output.width)]
        # alpha & 0xFE: 0→0, 1→0, 128→128, 255→254
        assert alpha == [0, 0, 128, 254]
        assert "Comment" not in output.info


def test_downscale_keeps_small_image_untouched() -> None:
    """验证未超过免费像素上限的图片不会被缩放。"""

    encoded = encode_png(Image.new("RGB", (512, 512), (10, 20, 30)))
    result, width, height = image_ops.downscale_to_free_tier(encoded)
    assert (width, height) == (512, 512)
    assert result == encoded


def test_downscale_aligns_to_64_within_budget() -> None:
    """验证超限图片缩放后对齐 64 像素且总像素不超上限。"""

    encoded = encode_png(Image.new("RGB", (2048, 2048), (0, 0, 0)))
    _, width, height = image_ops.downscale_to_free_tier(encoded)
    assert width % 64 == 0 and height % 64 == 0
    assert width * height <= image_ops.FREE_TIER_MAX_PIXELS


def test_downscale_aligns_unaligned_image_under_limit() -> None:
    """验证像素未超限但宽高未对齐 64 的图片也会被对齐（500 根因）。"""

    encoded = encode_png(Image.new("RGB", (1080, 508), (0, 0, 0)))
    _, width, height = image_ops.downscale_to_free_tier(encoded)
    assert width % 64 == 0 and height % 64 == 0
    assert width * height <= image_ops.FREE_TIER_MAX_PIXELS


@pytest.mark.parametrize(
    ("source_size", "expected_size"),
    [
        ((400, 800), (1024, 1536)),
        ((800, 400), (1536, 1024)),
        ((600, 600), (1472, 1472)),
    ],
)
def test_director_reference_uses_nearest_official_canvas(
    source_size: tuple[int, int],
    expected_size: tuple[int, int],
) -> None:
    """验证精密参考图按源比例选择官网 Q68 大画幅。"""

    encoded = encode_png(Image.new("RGB", source_size, (255, 255, 255)))
    fitted = image_ops.fit_for_director_reference(encoded)
    assert image_ops.read_image_size(fitted) == expected_size


@pytest.mark.parametrize(
    "invalid",
    [
        "data:image/png;base64,not-base64!",
        "data:text/plain;base64,SGVsbG8=",
        base64.b64encode(b"not an image").decode("ascii"),
    ],
)
def test_validate_image_data_rejects_invalid_inputs(invalid: str) -> None:
    """损坏 data URL、错误 MIME 和非图片 Base64 不得被接受。"""

    with pytest.raises(ValueError):
        image_ops.validate_image_data(invalid)


def test_preencoded_vibe_rejects_damaged_encoding(tmp_path: Path) -> None:
    """验证损坏的导出向量不会退化为普通图片或继续发送。"""

    vibe_file = tmp_path / "damaged.naiv4vibe"
    vibe_file.write_text(
        '{"encodings":{"v4-5full":{"0":{"encoding":"not-base64!"}}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="vibe_encoding"):
        assets.read_preencoded_vector(vibe_file, "nai-diffusion-4-5-full")


@pytest.mark.asyncio
async def test_preencoded_vibe_without_model_vector_never_calls_encoder(
    tmp_path: Path,
) -> None:
    """预编码文件缺少当前模型向量时不得回退到在线编码。"""

    vibe_file = tmp_path / "full-only.naiv4vibe"
    vibe_file.write_text(
        '{"image":"source-image","encodings":{"v4-5full":{}}}',
        encoding="utf-8",
    )
    encoder = AsyncMock(return_value="network-vector")
    library = assets.AssetLibrary()
    settings = cast(EngineSettings, SimpleNamespace(vibe_storage_dir=tmp_path))

    loaded = await library._load_vibes(
        settings,
        [VibeItemConfig(file=vibe_file.name)],
        encoder,
        "nai-diffusion-4-5-curated",
    )

    assert loaded == []
    encoder.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "available_models",
    [
        ["nai-diffusion-4-5-full", "nai-diffusion-4-5-curated"],
        ["nai-diffusion-4-5-curated", "nai-diffusion-4-5-full"],
    ],
)
async def test_preencoded_vibe_selects_vector_by_request_model_regardless_of_order(
    tmp_path: Path,
    available_models: list[str],
) -> None:
    """预编码 Vibe 按实际请求模型选向量，不依赖白名单顺序。"""

    vibe_file = tmp_path / "both.naiv4vibe"
    vibe_file.write_text(
        """{
            "encodings": {
                "v4-5full": {"0": {"encoding": "ZnVsbA=="}},
                "v4-5curated": {"0": {"encoding": "Y3VyYXRlZA=="}}
            }
        }""",
        encoding="utf-8",
    )
    config = ImageGeneratorConfig()
    config.api.api_keys = ["test-key"]
    config.generation.model = available_models[0]
    config.generation.available_models = available_models
    config.advanced.vibe_storage_dir = str(tmp_path)
    settings = EngineSettings.from_config(config)
    encoder = AsyncMock(return_value="network-vector")
    library = assets.AssetLibrary()

    await library.reload(
        settings,
        always_items=[],
        selectable_items=[VibeItemConfig(file=vibe_file.name)],
        director_items=[],
        encoder=encoder,
    )

    full = library.select_vibes(("both",), "v4-5full")
    curated = library.select_vibes(("both",), "v4-5curated")
    assert [asset.data for asset in full] == ["ZnVsbA=="]
    assert [asset.data for asset in curated] == ["Y3VyYXRlZA=="]
    encoder.assert_not_awaited()


def test_rect_mask_marks_only_selected_region() -> None:
    """验证矩形遮罩仅在指定区域为白色不透明。"""

    import base64

    mask_b64 = image_ops.build_rect_mask(100, 100, 0.5, 0.0, 0.5, 1.0)
    with Image.open(io.BytesIO(base64.b64decode(mask_b64))) as mask:
        assert mask.getpixel((10, 50)) == (0, 0, 0, 255)
        assert mask.getpixel((75, 50)) == (255, 255, 255, 255)


def test_rect_mask_aligns_to_latent_blocks() -> None:
    """验证遮罩边界对齐 8×8 latent 块，避免半重绘块产生灰色锯齿边。"""

    import base64

    # 0.33 比例在 832 宽下取整为 274，不是 8 的倍数，需对齐。
    mask_b64 = image_ops.build_rect_mask(832, 1216, 0.33, 0.33, 0.33, 0.33)
    with Image.open(io.BytesIO(base64.b64decode(mask_b64))) as mask:
        pixels = mask.convert("L")
        white_columns = [
            x
            for x in range(mask.width)
            if cast(int, pixels.getpixel((x, mask.height // 2))) > 127
        ]
        white_rows = [
            y
            for y in range(mask.height)
            if cast(int, pixels.getpixel((mask.width // 2, y))) > 127
        ]
        assert white_columns and white_rows
        assert white_columns[0] % 8 == 0
        assert (white_columns[-1] + 1) % 8 == 0
        assert white_rows[0] % 8 == 0
        assert (white_rows[-1] + 1) % 8 == 0


def test_draw_description_rebuilds_without_accumulating() -> None:
    """验证描述每次都从基础文本重建，配置刷新不会叠加历史内容。"""

    config = ImageGeneratorConfig()
    config.generation.style_reference = "anime style"
    config.prompt.presets = [
        PromptPresetConfig(name="自拍模式", trigger="画自己时", content="使用角色标签")
    ]

    first = build_draw_description(config)
    second = build_draw_description(config)
    assert first == second
    assert first.count("anime style") == 1
    assert "自拍模式" in first

    config.generation.style_reference = ""
    assert "anime style" not in build_draw_description(config)


def test_draw_description_hides_skip_hint_when_forced() -> None:
    """验证禁止跳过画风时展示强制注入提示。"""

    config = ImageGeneratorConfig()
    config.generation.style_reference = "anime style"
    config.generation.allow_skip_style = False
    assert "不可跳过" in build_draw_description(config)


def test_draw_description_detects_and_injects_v5_model() -> None:
    """验证 V5 模型说明覆盖完整专属能力，且不暴露参考素材列表。"""

    from image_generator_plugin_neo.config import DirectorReferenceItemConfig, VibeItemConfig

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-curated"
    config.vibe.selectable_enabled = True
    config.vibe.selectable = [
        VibeItemConfig(file="test_vibe.png", description="测试画风")
    ]
    config.director_reference.enabled = True
    config.director_reference.selectable_enabled = True
    config.director_reference.selectable = [
        DirectorReferenceItemConfig(file="test_ref.png", description="测试参考")
    ]
    desc = build_draw_description(config)

    assert "【默认生图模型（不指定 model 参数时生效）】" in desc
    assert "nai-diffusion-5-curated" in desc
    assert "NovelAI V5 Curated 专属规则" in desc
    assert "1471 Tokens" in desc
    assert "1.3~1.8" in desc
    assert "transparent background" in desc
    assert "visual novel sprite" in desc
    assert "V5 角色硬上限 32" in desc
    assert "最佳写法是混合提示词" in desc
    assert "完整英语自然语言句子" in desc
    assert "V5 不要求 V4.5 的 5×5 网格" in desc
    assert "连续归一化坐标" in desc
    assert "不要吸附或取整到 5×5 格点" in desc
    assert "不要强制套用 source#/target#/mutual#" in desc
    assert "visual novel bg 为纯背景" in desc
    assert "Steps 固定使用管理员配置" in desc
    assert "AI Action 不得覆盖" in desc
    assert "默认也不覆盖配置" in desc
    assert "Chunks 提示词收藏" in desc
    assert "不要虚构 chunks 或 enhance_max 字段" in desc
    assert "2026 年 7 月" in desc
    assert "Gotoh Hitori" in desc
    assert "「」" in desc
    assert "padoru_(meme)" in desc
    assert "three-panel comic page" in desc
    assert "0.1~0.8" in desc
    assert "不可完全重叠" in desc
    assert "TEXT:" not in desc
    # 验证 V5 架构下不向 LLM 暴露 Vibe 与精密参考列表
    assert "【可选 Vibe 画风列表" not in desc
    assert "【可用精密参考列表" not in desc


def test_draw_description_detects_and_injects_v4_model() -> None:
    """验证 V4/V4.5 模型时自动注入模型名与 V4.5 专属 TEXT: 语法。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-4-5-full"
    desc = build_draw_description(config)

    assert "【默认生图模型（不指定 model 参数时生效）】" in desc
    assert "nai-diffusion-4-5-full" in desc
    assert "NovelAI V4.5 Full 专属规则" in desc
    assert "505 Tokens" in desc
    assert "2025 年 6 月" in desc
    assert "1.1~1.4" in desc
    assert "TEXT:" in desc
    assert "5×5 网格站位" in desc
    assert "V4.5 角色互动语法（仅 V4.5）" in desc
    assert "transparent background" not in desc


def test_draw_description_includes_each_selectable_model_profile() -> None:
    """验证默认模型与白名单模型的规则会同时提供给 Bot。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-4-5-full"
    config.generation.available_models = [
        "nai-diffusion-4-5-full",
        "nai-diffusion-5-full",
    ]
    desc = build_draw_description(config)

    assert desc.count("nai-diffusion-4-5-full：NovelAI V4.5 Full 专属规则") == 1
    assert desc.count("nai-diffusion-5-full：NovelAI V5 Full 专属规则") == 1


def test_draw_prompt_parameter_follows_selected_model_language_rules() -> None:
    """验证 Action 参数不再用静态注解禁止 V5 多语言提示词。"""

    annotation = get_type_hints(DrawAction.execute, include_extras=True)[
        "content_description"
    ]
    description = annotation.__metadata__[0]
    assert "禁止中文" not in description
    assert "V5 可混合英文 Tag 与多语言自然语言" in description
    assert "英语自然语言描述复杂动作" in description


def test_ai_actions_do_not_expose_steps_override() -> None:
    """验证 AI Action 不能覆盖可能影响生成消耗的采样步数。"""

    from image_generator_plugin_neo.actions.edit import EditImageAction
    from image_generator_plugin_neo.actions.inpaint import InpaintAction

    for action in (DrawAction, EditImageAction, InpaintAction):
        parameters = get_type_hints(action.execute, include_extras=True)
        assert "steps" not in parameters
        assert "guidance" in parameters
        assert "pgr" in parameters
        assert "variety_plus" in parameters


# ─── media_id 精确取图测试 ───


async def test_extract_image_by_media_id_reads_cached_file(tmp_path: Path) -> None:
    """验证 media_id 命中媒体库时能读取文件并返回 base64。"""

    image_bytes = b"\x89PNG\r\n\x1a\nfakedata"
    image_file = tmp_path / "cached.png"
    image_file.write_bytes(image_bytes)
    expected_b64 = base64.b64encode(image_bytes).decode("utf-8")

    mock_get_media_info = AsyncMock(return_value={"path": str(image_file)})
    with patch(
        "image_generator_plugin_neo.media.message_images.get_media_info",
        mock_get_media_info,
    ):
        result = await extract_image_by_media_id("abc123")
    assert result == expected_b64
    mock_get_media_info.assert_awaited_once_with("abc123")


async def test_extract_image_by_media_id_returns_none_when_not_found() -> None:
    """验证 media_id 未命中媒体库时返回 None。"""

    mock_get_media_info = AsyncMock(return_value=None)
    with patch(
        "image_generator_plugin_neo.media.message_images.get_media_info",
        mock_get_media_info,
    ):
        result = await extract_image_by_media_id("nonexistent")
    assert result is None


async def test_extract_image_by_media_id_returns_none_when_file_missing(
    tmp_path: Path,
) -> None:
    """验证 media_id 有记录但文件不存在时返回 None。"""

    mock_get_media_info = AsyncMock(
        return_value={"path": str(tmp_path / "ghost.png")}
    )
    with patch(
        "image_generator_plugin_neo.media.message_images.get_media_info",
        mock_get_media_info,
    ):
        result = await extract_image_by_media_id("abc456")
    assert result is None


async def test_extract_image_by_media_id_returns_none_when_no_path() -> None:
    """验证 media_id 记录中无 path 字段时返回 None。"""

    mock_get_media_info = AsyncMock(return_value={"path": ""})
    with patch(
        "image_generator_plugin_neo.media.message_images.get_media_info",
        mock_get_media_info,
    ):
        result = await extract_image_by_media_id("abc789")
    assert result is None
