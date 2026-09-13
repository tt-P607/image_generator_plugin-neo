"""引擎配置快照与请求体构造测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from PIL import Image

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.engine.engine import ImageEngine
from image_generator_plugin_neo.engine import payload as payload_builder
from image_generator_plugin_neo.engine.settings import EngineSettings
from image_generator_plugin_neo.engine.types import (
    CharacterPrompt,
    DirectorRefAsset,
    DirectorToolSpec,
    EnhanceSpec,
    GenerationSpec,
    ImageResult,
    InpaintSpec,
    UserVibeStore,
    VibeAsset,
)
from image_generator_plugin_neo.media import enhance as enhance_ops


def encoded_image(width: int = 832, height: int = 1216) -> str:
    """构造测试用 PNG Base64。"""

    import base64
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def make_settings(**overrides: object) -> EngineSettings:
    """按默认配置构造引擎快照，可覆盖个别字段。"""

    config = ImageGeneratorConfig()
    settings = EngineSettings.from_config(config)
    if not overrides:
        return settings
    values = {field: getattr(settings, field) for field in settings.__slots__}
    values.update(overrides)
    return EngineSettings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_engine_uses_official_character_limit_from_selected_model() -> None:
    """验证 V5 可使用 32 个角色且不会再受全局配置限制。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-full"
    config.generation.available_models = [
        "nai-diffusion-5-full",
        "nai-diffusion-4-5-full",
    ]
    engine = ImageEngine(config)
    engine._submit = AsyncMock(return_value=object())  # type: ignore[method-assign]
    v5_characters = tuple(
        CharacterPrompt(prompt=f"character {index}") for index in range(32)
    )

    await engine.generate(
        GenerationSpec(
            prompt="group",
            user_id="tester",
            characters=v5_characters,
        )
    )
    engine._submit.assert_awaited_once()

    too_many_v45 = tuple(
        CharacterPrompt(prompt=f"character {index}") for index in range(7)
    )
    result = await engine.generate(
        GenerationSpec(
            prompt="group",
            user_id="tester",
            model="nai-diffusion-4-5-full",
            characters=too_many_v45,
        )
    )
    assert result.success is False
    assert "官方限制" in result.message


def test_gateway_root_normalizes_v1_suffix() -> None:
    """验证 gateway 根地址会去掉 /v1 与末尾斜杠。"""

    assert make_settings(base_url="http://127.0.0.1:31555/v1").gateway_root == (
        "http://127.0.0.1:31555"
    )
    assert make_settings(base_url="http://127.0.0.1:31555/").gateway_root == (
        "http://127.0.0.1:31555"
    )


def test_official_urls_derive_from_base_url() -> None:
    """验证 official 渠道各端点由 base_url 推导。"""

    settings = make_settings(model="nai-diffusion-4-5-full")
    assert settings.official_generate_url.endswith("/ai/generate-image")
    assert settings.official_encode_vibe_url.endswith("/ai/encode-vibe")
    assert settings.official_augment_url.endswith("/ai/augment-image")
    assert settings.official_upscale_url.endswith("/ai/upscale")
    assert settings.official_subscription_url.endswith("/user/subscription")


def test_upscale_payloads_match_channel_protocols() -> None:
    """验证 upscale 请求体：两种渠道均使用固定 2× 放大协议。"""

    official = payload_builder.build_official_upscale("img", 512, 512)
    assert official == {
        "image": "img",
        "model": "nai-diffusion-5-curated",
        "declared_blur_sigma": 0,
    }

    gateway = payload_builder.build_gateway_upscale("img", 512, 512, "nai-diffusion-4-5-full")
    assert gateway == {
        "model": "nai-diffusion-4-5-full",
        "extra": "upscale",
        "image": "img",
        "response_format": "b64_json",
    }


def test_enhance_planner_matches_official_scale_and_alignment_rules() -> None:
    """验证官网 Enhance 倍率候选、64 对齐与提示词片段。"""

    assert enhance_ops.available_scales(832, 1216, supports_max=False) == (
        "1x",
        "1.5x",
    )
    assert enhance_ops.available_scales(832, 1216, supports_max=True) == (
        "1x",
        "1.5x",
        "Max",
    )
    assert enhance_ops.pipeline_dimensions(832, 1216, "1.5x") == (1280, 1856)
    assert enhance_ops.pipeline_dimensions(801, 1000, "Max") == (832, 1024)
    assert enhance_ops.append_prompt_suffix("1girl") == (
        "1girl, -2::upscaled, blurry::,"
    )


@pytest.mark.asyncio
async def test_enhance_builds_single_img2img_request_for_max() -> None:
    """验证 V5 Max 构造独立单图 img2img 请求。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-curated"
    engine = ImageEngine(config)
    engine._run_generate = AsyncMock(  # type: ignore[method-assign]
        return_value=ImageResult.ok("enhanced.png")
    )

    async def submit_now(work: object) -> ImageResult:
        """测试中立即执行队列闭包。"""

        return await work()  # type: ignore[operator]

    engine._submit = AsyncMock(side_effect=submit_now)  # type: ignore[method-assign]
    result = await engine.enhance(
        EnhanceSpec(
            prompt="1girl",
            user_id="tester",
            source_image=encoded_image(801, 1000),
            scale="Max",
            strength=0.5,
            noise=0.0,
            seed=0,
        )
    )

    assert result.success is True
    generation = engine._run_generate.await_args.args[0]
    assert (generation.width, generation.height) == (832, 1024)
    assert generation.seed == 0
    assert generation.noise == 0.0
    assert generation.upscaled_enhance is True
    assert engine._run_generate.await_args.kwargs == {"prepare_img2img": False}


@pytest.mark.asyncio
async def test_v45_rejects_max_enhance_before_queue() -> None:
    """验证 V4.5 不允许 Max Enhance。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-4-5-full"
    engine = ImageEngine(config)
    engine._submit = AsyncMock()  # type: ignore[method-assign]
    result = await engine.enhance(
        EnhanceSpec(
            prompt="1girl",
            user_id="tester",
            source_image=encoded_image(),
            scale="Max",
        )
    )
    assert result.success is False
    assert "enhance_scale" in result.message
    engine._submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_enhance_rejects_unsupported_model_and_invalid_seed() -> None:
    """验证 Engine 最终边界拒绝不支持 Enhance 的模型及非法 seed。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-4-full"
    engine = ImageEngine(config)
    source = encoded_image()
    unsupported = await engine.enhance(
        EnhanceSpec(
            prompt="1girl",
            user_id="tester",
            source_image=source,
            scale="1x",
        )
    )
    assert unsupported.success is False
    assert "nai-diffusion-4-full" in unsupported.message
    assert "enhance_scale" in unsupported.message

    config.generation.model = "nai-diffusion-5-curated"
    engine = ImageEngine(config)
    invalid_seed = await engine.enhance(
        EnhanceSpec(
            prompt="1girl",
            user_id="tester",
            source_image=source,
            scale="1x",
            seed=1_000_000_000,
        )
    )
    assert invalid_seed.success is False
    assert "nai-diffusion-5-curated" in invalid_seed.message
    assert "seed" in invalid_seed.message


@pytest.mark.asyncio
async def test_generate_many_uses_sequential_single_requests_and_increments_seed() -> None:
    """验证多图由插件逐张调用单图生成并递增显式 seed。"""

    engine = ImageEngine(ImageGeneratorConfig())
    engine.generate = AsyncMock(  # type: ignore[method-assign]
        side_effect=[ImageResult.ok(f"{index}.png") for index in range(3)]
    )
    results = await engine.generate_many(
        GenerationSpec(prompt="1girl", user_id="tester", seed=0),
        3,
    )
    assert len(results) == 3
    assert [call.args[0].seed for call in engine.generate.await_args_list] == [0, 1, 2]


def test_enhance_payload_preserves_zero_and_gateway_single_count() -> None:
    """验证 Enhance 的 seed/noise 显式零及 Gateway n=1。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        source_image=encoded_image(),
        strength=0.5,
        noise=0.0,
        seed=0,
        upscaled_enhance=True,
    )
    settings = make_settings(model="nai-diffusion-5-curated")
    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert official["parameters"]["seed"] == 0
    assert official["parameters"]["noise"] == 0.0
    assert official["parameters"]["upscaled_enhance"] is True
    assert gateway["n"] == 1
    assert gateway["params"]["seed"] == 0
    assert gateway["noise"] == 0.0
    assert gateway["params"]["upscaled_enhance"] is True


def test_output_dir_switches_by_source() -> None:
    """验证命令产图与 Action 产图落在不同目录。"""

    settings = make_settings()
    assert settings.output_dir(from_command=True) == settings.command_images_dir
    assert settings.output_dir(from_command=False) == settings.temp_dir


def test_merge_negative_prompts_keeps_order_and_dedupes() -> None:
    """验证负面词合并保留基础顺序且去重。"""

    merged = payload_builder.merge_negative_prompts("blurry, text", "TEXT, chibi")
    assert merged == "blurry, text, chibi"
    assert payload_builder.merge_negative_prompts("", "chibi") == "chibi"
    assert payload_builder.merge_negative_prompts("blurry", None) == "blurry"


def test_v45_character_positions_snap_to_official_grid_in_both_channels() -> None:
    """验证 V4.5 双渠道角色坐标吸附到官网 5×5 单元中心。"""

    settings = make_settings(model="nai-diffusion-4-5-full")
    spec = GenerationSpec(
        prompt="2girls, outdoor",
        user_id="tester",
        characters=(
            CharacterPrompt(
                prompt="1girl, red hair",
                negative_prompt="bad hands",
                x=0.19,
                y=1.0,
            ),
            CharacterPrompt(prompt="1girl, blue hair", x=0.79, y=-0.1),
        ),
    )

    official = payload_builder.build_official_generation(settings, spec, ())
    parameters = official["parameters"]
    gateway = payload_builder.build_gateway_generation(settings, spec)

    assert official["action"] == "generate"
    assert len(parameters["characterPrompts"]) == 2
    assert parameters["characterPrompts"][0]["center"] == {"x": 0.1, "y": 0.9}
    assert parameters["characterPrompts"][1]["center"] == {"x": 0.7, "y": 0.1}
    assert parameters["use_coords"] is True
    assert parameters["v4_prompt"]["caption"]["char_captions"][1]["char_caption"] == (
        "1girl, blue hair"
    )
    assert gateway["params"]["characters"][0]["center"] == {"x": 0.1, "y": 0.9}
    assert gateway["params"]["characters"][1]["center"] == {"x": 0.7, "y": 0.1}


def test_v5_character_positions_preserve_continuous_coordinates() -> None:
    """验证 V5 双渠道原样保留非 5×5 网格的自由小数坐标。"""

    settings = make_settings(model="nai-diffusion-5-full")
    spec = GenerationSpec(
        prompt="2girls, outdoor",
        user_id="tester",
        characters=(
            CharacterPrompt(prompt="1girl, red hair", x=0.17, y=0.43),
            CharacterPrompt(prompt="1girl, blue hair", x=0.86, y=0.72),
        ),
    )

    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)

    assert official["parameters"]["characterPrompts"][0]["center"] == {
        "x": 0.17,
        "y": 0.43,
    }
    assert official["parameters"]["characterPrompts"][1]["center"] == {
        "x": 0.86,
        "y": 0.72,
    }
    assert gateway["params"]["characters"][0]["center"] == {"x": 0.17, "y": 0.43}
    assert gateway["params"]["characters"][1]["center"] == {"x": 0.86, "y": 0.72}
    assert "position" not in gateway["params"]["characters"][0]
    assert "position" not in gateway["params"]["characters"][1]


def test_official_generation_switches_to_img2img() -> None:
    """验证带原图时切换为 img2img 且叠加原图。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        source_image="base64-image",
        strength=0.55,
    )
    body = payload_builder.build_official_generation(make_settings(), spec, ())

    assert body["action"] == "img2img"
    assert body["parameters"]["strength"] == 0.55
    assert body["parameters"]["add_original_image"] is True


def test_official_generation_prefers_director_refs_over_vibes() -> None:
    """验证精密参考存在时不再注入 Vibe 字段。"""

    settings = make_settings(model="nai-diffusion-4-5-full", vibe_always_enabled=True)
    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        director_refs=(
            DirectorRefAsset(
                data="ref-data",
                ref_type="character&style",
                fidelity=0.75,
                strength=0.8,
            ),
        ),
    )

    body = payload_builder.build_official_generation(
        settings,
        spec,
        (VibeAsset(data="vibe", information_extracted=1.0, strength=0.6),),
    )
    parameters = body["parameters"]

    assert parameters["director_reference_images"] == ["ref-data"]
    assert parameters["director_reference_strength_values"] == [0.8]
    assert parameters["director_reference_secondary_strength_values"] == [0.25]
    assert parameters["reference_image_multiple"] == []


def test_official_inpaint_uses_infill_model_and_keeps_original() -> None:
    """验证局部重绘切换到 inpainting 模型并叠加原图。"""

    spec = InpaintSpec(
        prompt="1girl, pink dress",
        source_image="image",
        mask="mask",
        strength=0.6,
    )
    body = payload_builder.build_official_inpaint(make_settings(), spec)

    assert body["action"] == "infill"
    assert body["model"].endswith("-inpainting")
    assert body["parameters"]["add_original_image"] is True
    assert body["parameters"]["mask"] == "mask"


def test_v45_inpaint_keeps_director_reference_in_both_channels() -> None:
    """验证 V4.5 局部重绘在双渠道中保留精密角色参考。"""

    reference = DirectorRefAsset(
        data="reference-image",
        ref_type="character",
        fidelity=0.0,
        strength=0.0,
        information_extracted=0.0,
    )
    spec = InpaintSpec(
        prompt="1girl, pink dress",
        source_image="image",
        mask="mask",
        strength=0.6,
        model="nai-diffusion-4-5-full",
        director_refs=(reference,),
    )
    settings = make_settings(model="nai-diffusion-4-5-full")

    official = payload_builder.build_official_inpaint(settings, spec)
    official_params = official["parameters"]
    gateway = payload_builder.build_gateway_inpaint(settings, spec)

    assert official_params["director_reference_images"] == ["reference-image"]
    assert official_params["director_reference_strength_values"] == [0.0]
    assert official_params["director_reference_secondary_strength_values"] == [1.0]
    assert official_params["director_reference_information_extracted"] == [0.0]
    assert gateway["n"] == 1
    assert gateway["params"]["character_references"] == [
        {
            "image": "reference-image",
            "type": "character",
            "strength": 0.0,
            "fidelity": 0.0,
            "information_extracted": 0.0,
        }
    ]


def test_gateway_generation_matches_openai_image_schema() -> None:
    """验证 Gateway 文生图字段符合新版 OpenAI 图片接口约定（参数收进 params）。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        negative_prompt="text",
        width=832,
        height=1216,
        characters=(CharacterPrompt(prompt="1girl, red hair", x=0.3),),
        director_refs=(
            DirectorRefAsset(
                data="ref",
                ref_type="character&style",
                fidelity=0.75,
                strength=0.8,
            ),
        ),
    )
    body = payload_builder.build_gateway_generation(
        make_settings(model="nai-diffusion-4-5-full"), spec
    )
    params = body["params"]

    # 顶层仅保留 OpenAI 通用字段
    assert body["model"] == "nai-diffusion-4-5-full"
    assert body["prompt"] == "1girl"
    assert body["n"] == 1
    assert body["size"] == "832x1216"

    # NovelAI 专属参数统一在 params 中；负面词为全局内置词与本次额外词的合并
    assert params["steps"] > 0
    assert params["sampler"]
    assert params["quality"] is True
    assert params["uc_preset"] in {"strong", "light", "furry_focus", "human_focus", "none"}
    assert "text," in f"{params['negative_prompt']},"
    assert params["characters"][0]["center"] == {"x": 0.3, "y": 0.5}
    assert "position" not in params["characters"][0]
    assert params["character_references"][0]["fidelity"] == 0.75


def test_gateway_generation_with_vibes_injects_reference_fields() -> None:
    """验证 Gateway 文生图附带 Vibe 时在 params 注入参考图字段。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        width=832,
        height=1216,
    )
    vibes = (VibeAsset(data="vibe-data", information_extracted=1.0, strength=0.6),)
    body = payload_builder.build_gateway_generation(
        make_settings(model="nai-diffusion-4-5-full"), spec, vibes
    )
    params = body["params"]

    assert params["reference_image_multiple"] == ["vibe-data"]
    assert params["reference_strength_multiple"] == [0.6]
    assert params["reference_information_extracted_multiple"] == [1.0]


def test_gateway_generation_img2img_includes_image_field() -> None:
    """验证 Gateway 图生图通过顶层 image 字段触发，不再使用独立端点。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        source_image="base64-image",
        strength=0.7,
    )
    body = payload_builder.build_gateway_generation(make_settings(), spec)

    assert body["image"] == "base64-image"
    assert body["strength"] == 0.7


def test_gateway_director_uses_unified_endpoint_with_extra() -> None:
    """验证导演工具通过 extra 字段路由到统一端点。"""

    colorize = payload_builder.build_gateway_director(
        DirectorToolSpec(
            tool_type="colorize",
            source_image="image",
            width=1024,
            height=1024,
            prompt="warm tones",
            defry=0,
        ),
        "nai-diffusion-4-5-full",
    )
    assert colorize["extra"] == "director-colorize"
    assert colorize["model"] == "nai-diffusion-4-5-full"
    assert colorize["prompt"] == "warm tones"
    assert colorize["defry"] == 0

    lineart = payload_builder.build_gateway_director(
        DirectorToolSpec(
            tool_type="lineart",
            source_image="image",
            width=1024,
            height=1024,
            prompt="ignored",
            defry=3,
        ),
        "nai-diffusion-4-5-full",
    )
    assert lineart["extra"] == "director-lineart"
    assert "prompt" not in lineart
    assert "defry" not in lineart


def test_user_vibe_store_respects_limit_and_isolation() -> None:
    """验证手动 Vibe 按用户隔离并受数量上限约束。"""

    store = UserVibeStore()
    asset = VibeAsset(data="v", information_extracted=1.0, strength=0.6)

    assert store.add("alice", asset, limit=2) == (True, 1)
    assert store.add("alice", asset, limit=2) == (True, 2)
    assert store.add("alice", asset, limit=2) == (False, 2)
    assert store.get("bob") == []

    store.clear("alice")
    assert store.get("alice") == []


def test_vibe_asset_carries_optional_name() -> None:
    """验证 VibeAsset 的 name 字段用于日志展示且可缺省。"""

    unnamed = VibeAsset(data="v", information_extracted=1.0, strength=0.6)
    named = VibeAsset(
        data="v",
        information_extracted=1.0,
        strength=0.6,
        name="日系块面厚涂概念插画风",
    )

    assert unnamed.name == ""
    assert named.name == "日系块面厚涂概念插画风"


def test_vibe_model_uses_supported_model_from_whitelist() -> None:
    """验证默认 V5 时会从白名单中选择 V4.5 作为 Vibe 编码模型。"""

    settings = make_settings(
        model="nai-diffusion-5-curated",
        available_models=(
            "nai-diffusion-5-curated",
            "nai-diffusion-4-5-full",
        ),
    )
    assert settings.vibe_model == "nai-diffusion-4-5-full"

    v5_only = make_settings(
        model="nai-diffusion-5-curated",
        available_models=("nai-diffusion-5-curated",),
    )
    assert v5_only.vibe_model is None


def test_alias_model_resolves_profile_and_keeps_request_name() -> None:
    """验证别名模型按官方档案判断能力，请求体保留自定义模型名。"""

    config = ImageGeneratorConfig()
    config.generation.model = "my-v5"
    config.generation.available_models = ["my-v5"]
    config.generation.model_aliases = {"my-v5": "nai-diffusion-5-full"}
    settings = EngineSettings.from_config(config)

    assert settings.resolve_model("my-v5") == "nai-diffusion-5-full"
    assert settings.model_profile("my-v5").is_v5 is True
    assert settings.model_profile("my-v5").max_characters == 32
    assert settings.model_profile("my-v5").supports_vibe is False

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        characters=(CharacterPrompt(prompt="1girl, red hair", x=0.3),),
    )
    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)

    assert official["model"] == "my-v5"
    assert gateway["model"] == "my-v5"
    assert official["parameters"]["noise_schedule"] == "karras"
    assert gateway["params"]["noise_schedule"] == "karras"
    assert official["parameters"]["v4_prompt"]["use_coords"] is True


def test_keyword_inference_resolves_third_party_model_names() -> None:
    """验证含版本关键词的第三方模型名可自动推断能力档案。"""

    config = ImageGeneratorConfig()
    config.generation.model = "proxy/novelai-4.5-curated"
    config.generation.available_models = [
        "proxy/novelai-4.5-curated",
        "some-v5-model",
    ]
    settings = EngineSettings.from_config(config)

    v45 = settings.model_profile("proxy/novelai-4.5-curated")
    assert v45.family == "v4.5"
    assert v45.edition == "curated"
    assert v45.supports_vibe is True
    assert v45.max_characters == 6

    v5 = settings.model_profile("some-v5-model")
    assert v5.is_v5 is True
    assert v5.edition == "full"
    assert v5.max_characters == 32
    assert v5.supports_vibe is False

    spec = GenerationSpec(prompt="1girl", user_id="tester")
    official = payload_builder.build_official_generation(settings, spec, ())
    assert official["model"] == "proxy/novelai-4.5-curated"


def test_unrecognizable_model_name_fails_clearly() -> None:
    """验证无版本关键词且无别名的模型名返回明确错误。"""

    config = ImageGeneratorConfig()
    config.generation.model = "totally-unknown-model"
    with pytest.raises(ValueError, match="无法识别模型"):
        EngineSettings.from_config(config)


def test_from_config_validates_sampling_matrix_for_all_allowed_models() -> None:
    """验证引擎快照构造时会校验白名单内每个模型的采样组合。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-full"
    config.generation.available_models = [
        "nai-diffusion-5-full",
        "nai-diffusion-4-5-full",
    ]
    config.generation.sampler = "k_dpm_2"
    config.generation.noise_schedule = "karras"

    with pytest.raises(ValueError, match="不支持噪声调度"):
        EngineSettings.from_config(config)


def test_alias_model_inpainting_derives_from_alias_name() -> None:
    """验证别名模型的局部重绘映射到官方 Inpainting 模型。"""

    config = ImageGeneratorConfig()
    config.generation.model = "my-v45"
    config.generation.model_aliases = {"my-v45": "nai-diffusion-4-5-full"}
    settings = EngineSettings.from_config(config)

    spec = InpaintSpec(
        prompt="1girl, pink dress",
        source_image="image",
        mask="mask",
        strength=0.6,
    )
    body = payload_builder.build_official_inpaint(settings, spec)

    assert body["model"] == "nai-diffusion-4-5-full-inpainting"
    assert body["action"] == "infill"


def test_rule_reminder_switch_defaults_off() -> None:
    """验证生图规则注入开关默认关闭。"""

    config = ImageGeneratorConfig()
    assert config.prompt.inject_rule_reminder is False


def test_v5_model_is_recognized_and_inpaint_mapped() -> None:
    """验证 V5 模型被识别为结构化架构且 inpainting 映射正确。"""

    settings = make_settings(model="nai-diffusion-5-curated")
    assert settings.is_v4_model is True
    assert settings.is_v5_model is True
    assert settings.supports_vibes is False
    assert settings.supports_director_refs is False

    spec = InpaintSpec(
        prompt="1girl, smile",
        source_image="base64img",
        mask="base64mask",
        strength=0.7,
    )
    payload = payload_builder.build_official_inpaint(settings, spec)
    assert payload["model"] == "nai-diffusion-5-full-inpainting"
    assert "v4_prompt" in payload["parameters"]


def test_v5_model_filters_out_vibes_and_director_refs() -> None:
    """验证 V5 模型生成请求中彻底过滤屏蔽 Vibe 和精密参考参数，防止 400 报错。"""

    settings = make_settings(model="nai-diffusion-5-full")
    spec = GenerationSpec(
        prompt="1girl, pink hair",
        user_id="tester",
        director_refs=(
            DirectorRefAsset(
                data="ref-data",
                ref_type="character",
                fidelity=0.8,
                strength=0.9,
            ),
        ),
    )
    vibes = (VibeAsset(data="vibe-data", information_extracted=1.0, strength=0.6),)

    # official 渠道验证
    official_payload = payload_builder.build_official_generation(settings, spec, vibes)
    params = official_payload["parameters"]
    assert "reference_image_multiple" not in params
    assert "director_reference_images" not in params

    # gateway 渠道验证（新格式 NovelAI 参数均在 params 内）
    gateway_payload = payload_builder.build_gateway_generation(settings, spec, vibes)
    assert "reference_image_multiple" not in gateway_payload["params"]
    assert "character_references" not in gateway_payload["params"]


def test_gateway_per_request_model_overrides_settings_model() -> None:
    """验证 Gateway 渠道下 spec.model 覆盖默认模型。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        model="nai-diffusion-4-5-full",
    )
    body = payload_builder.build_gateway_generation(
        make_settings(model="nai-diffusion-5-full"), spec
    )

    assert body["model"] == "nai-diffusion-4-5-full"
    assert body["prompt"] == "1girl"


def test_official_generation_per_request_model_switches_schema() -> None:
    """验证 official 渠道下 spec.model 为 V3 时按 V3 schema 构建请求体。"""

    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        model="nai-diffusion-3",
    )
    settings = make_settings(model="nai-diffusion-5-full")
    body = payload_builder.build_official_generation(settings, spec, ())

    assert body["model"] == "nai-diffusion-3"
    assert body["parameters"]["noise_schedule"] == "karras"
    assert "v4_prompt" not in body["parameters"]
    assert "characterPrompts" not in body["parameters"]


def test_generation_overrides_apply_to_both_channels() -> None:
    """验证调用级参数会同时覆盖 Official 与 Gateway 的全局配置。"""

    spec = GenerationSpec(
        prompt='1girl, holding sign, "Welcome"',
        user_id="tester",
        model="nai-diffusion-4-5-full",
        steps=24,
        scale=6.0,
        cfg_rescale=0.2,
        variety_plus=True,
        render_text=True,
    )
    settings = make_settings(
        model="nai-diffusion-4-5-full",
        steps=28,
        scale=5.0,
        cfg_rescale=0.0,
        variety_plus=False,
    )

    official = payload_builder.build_official_generation(settings, spec, ())
    official_params = official["parameters"]
    assert official_params["steps"] == 24
    assert official_params["scale"] == 6.0
    assert official_params["cfg_rescale"] == 0.2
    assert official_params["skip_cfg_above_sigma"] == 58
    assert "text" not in {
        tag.strip().lower()
        for tag in official_params["negative_prompt"].split(",")
    }

    gateway = payload_builder.build_gateway_generation(settings, spec)
    gateway_params = gateway["params"]
    assert gateway_params["steps"] == 24
    assert gateway_params["scale"] == 6.0
    assert gateway_params["cfg_rescale"] == 0.2
    assert gateway_params["variety_boost"] is True
    assert "text" not in {
        tag.strip().lower()
        for tag in gateway_params["negative_prompt"].split(",")
    }


async def test_empty_model_list_allows_only_default_model() -> None:
    """验证空白名单表示仅允许默认模型，不会放开任意模型。"""

    config = ImageGeneratorConfig()
    assert EngineSettings.from_config(config).allowed_models == (
        "nai-diffusion-5-curated",
    )

    engine = ImageEngine(config)
    result = await engine.generate(
        GenerationSpec(
            prompt="1girl",
            user_id="tester",
            model="nai-diffusion-4-5-full",
        )
    )
    assert result.success is False
    assert "不在可选列表" in result.message


def test_v5_multi_character_payload_keeps_structured_fields() -> None:
    """验证 V5 模型多人物请求保留结构化 characterPrompts 字段（支持多人物）。"""

    settings = make_settings(model="nai-diffusion-5-full")
    spec = GenerationSpec(
        prompt="2girls, outdoor",
        user_id="tester",
        characters=(CharacterPrompt(prompt="1girl, red hair", x=0.3),),
    )
    body = payload_builder.build_official_generation(settings, spec, ())
    parameters = body["parameters"]

    assert len(parameters["characterPrompts"]) == 1
    assert parameters["use_coords"] is True
    assert "v4_prompt" in parameters
