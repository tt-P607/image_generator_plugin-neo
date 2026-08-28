"""命令解析、图片处理与描述构建测试。"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

from PIL import Image, PngImagePlugin

from image_generator_plugin_neo.commands import parsing
from image_generator_plugin_neo.config import ImageGeneratorConfig, PromptPresetConfig
from image_generator_plugin_neo.descriptions import build_draw_description
from image_generator_plugin_neo.engine import storage
from image_generator_plugin_neo.media import extract_image_by_media_id, image_ops


def encode_png(image: Image.Image) -> str:
    """把 Pillow 图片编码为 base64 PNG。"""

    import base64

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_extract_scale_flags_keeps_zero_value() -> None:
    """验证 --rescale 0 不会被当成未设置。"""

    flags = parsing.extract_scale_flags("1girl --scale 7 --rescale 0")
    assert flags.scale == 7.0
    assert flags.cfg_rescale == 0.0
    assert flags.remainder == "1girl"


def test_split_prompt_supports_fullwidth_colon() -> None:
    """验证正负面切分兼容全角冒号。"""

    assert parsing.split_prompt("正面：1girl 负面：chibi") == ("1girl", "chibi")
    assert parsing.split_prompt("1girl, pink hair") == ("1girl, pink hair", None)


def test_parse_size_token_handles_aliases_and_literals() -> None:
    """验证画幅词元同时支持中文别名与显式尺寸。"""

    assert parsing.parse_size_token("竖图") == (832, 1216)
    assert parsing.parse_size_token("1216×832") == (1216, 832)
    assert parsing.parse_size_token("1girl") is None


def test_parse_edit_args_extracts_strength_only_in_range() -> None:
    """验证只有落在合法区间的数字才会被视作重绘强度。"""

    assert parsing.parse_edit_args(["1girl", "0.5"]) == ("1girl", 0.5)
    assert parsing.parse_edit_args(["1girl", "5"]) == ("1girl 5", None)
    assert parsing.parse_edit_args([]) == (parsing.DEFAULT_EDIT_PROMPT, None)


def test_extract_reference_flags_clamps_values() -> None:
    """验证参考参数会被夹紧到 0~1 且支持中文别名。"""

    flags = parsing.extract_reference_flags(
        "1girl --参考类型 风格 --fidelity 2.0 --strength -1"
    )
    assert flags.ref_type == "style"
    assert flags.fidelity == 1.0
    assert flags.strength == 0.0
    assert flags.remainder == "1girl"


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


def test_director_reference_is_padded_to_required_size() -> None:
    """验证精密参考图会被填充到 API 要求的 1024x1536。"""

    encoded = encode_png(Image.new("RGB", (800, 400), (255, 255, 255)))
    fitted = image_ops.fit_for_director_reference(encoded)
    assert image_ops.read_image_size(fitted) == image_ops.DIRECTOR_REF_SIZE


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


def test_rename_with_stem_avoids_overwriting(tmp_path: Path) -> None:
    """验证重名时自动追加序号，不覆盖已有文件。"""

    first = tmp_path / "a.png"
    first.write_bytes(b"first")
    existing = tmp_path / "portrait.png"
    existing.write_bytes(b"existing")

    renamed = storage.rename_with_stem(first, "portrait")
    assert renamed.name == "portrait_2.png"
    assert existing.read_bytes() == b"existing"


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
    """验证 V5 模型时自动注入模型名与 V5 专属规范，且不暴露 Vibe/精密参考列表。"""

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
    assert "NovelAI V5 架构" in desc
    assert "“你好，世界！”" in desc
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
    assert "NovelAI V4 / V4.5 架构" in desc
    assert "TEXT:" in desc
    assert "“你好，世界！”" not in desc


def test_draw_description_detects_and_injects_v3_model() -> None:
    """验证 V3 模型时自动注入模型名与 V3 纯文本规范。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-3"
    desc = build_draw_description(config)

    assert "【默认生图模型（不指定 model 参数时生效）】" in desc
    assert "nai-diffusion-3" in desc
    assert "NovelAI V3 架构" in desc
    assert "纯文本标签" in desc


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
