"""引擎配置快照与请求体构造测试。"""

from __future__ import annotations

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.engine import payload as payload_builder
from image_generator_plugin_neo.engine.settings import EngineSettings
from image_generator_plugin_neo.engine.types import (
    CharacterPrompt,
    DirectorRefAsset,
    DirectorToolSpec,
    GenerationSpec,
    InpaintSpec,
    UserVibeStore,
    VibeAsset,
)


def make_settings(**overrides: object) -> EngineSettings:
    """按默认配置构造引擎快照，可覆盖个别字段。"""

    config = ImageGeneratorConfig()
    settings = EngineSettings.from_config(config)
    if not overrides:
        return settings
    values = {field: getattr(settings, field) for field in settings.__slots__}
    values.update(overrides)
    return EngineSettings(**values)  # type: ignore[arg-type]


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

    settings = make_settings()
    assert settings.official_generate_url.endswith("/ai/generate-image")
    assert settings.official_encode_vibe_url.endswith("/ai/encode-vibe")
    assert settings.official_augment_url.endswith("/ai/augment-image")
    assert settings.official_upscale_url.endswith("/ai/upscale")
    assert settings.official_subscription_url.endswith("/user/subscription")


def test_upscale_payloads_match_channel_protocols() -> None:
    """验证 upscale 请求体：official 固定 scale=4，gateway 走统一端点带 extra。"""

    official = payload_builder.build_official_upscale("img", 512, 512)
    assert official == {"image": "img", "width": 512, "height": 512, "scale": 4}

    gateway = payload_builder.build_gateway_upscale("img", 512, 512, "nai-diffusion-4-5-full")
    assert gateway == {
        "model": "nai-diffusion-4-5-full",
        "extra": "upscale",
        "image": "img",
        "width": 512,
        "height": 512,
        "response_format": "b64_json",
    }


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


def test_official_generation_fills_v4_multi_character_fields() -> None:
    """验证 V4 多人物会同时写入 characterPrompts 与 char_captions。"""

    settings = make_settings()
    spec = GenerationSpec(
        prompt="2girls, outdoor",
        user_id="tester",
        characters=(
            CharacterPrompt(prompt="1girl, red hair", negative_prompt="bad hands", x=0.3),
            CharacterPrompt(prompt="1girl, blue hair", x=0.7),
        ),
    )

    body = payload_builder.build_official_generation(settings, spec, ())
    parameters = body["parameters"]

    assert body["action"] == "generate"
    assert len(parameters["characterPrompts"]) == 2
    assert parameters["characterPrompts"][0]["center"] == {"x": 0.3, "y": 0.5}
    assert parameters["use_coords"] is True
    assert parameters["v4_prompt"]["caption"]["char_captions"][1]["char_caption"] == (
        "1girl, blue hair"
    )


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
    assert params["uc_preset"] >= 0
    assert "text," in f"{params['negative_prompt']},"
    assert params["characters"][0]["position"] == [0.3, 0.5]
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
            defry=9,
        ),
        "nai-diffusion-4-5-full",
    )
    assert colorize["extra"] == "director-colorize"
    assert colorize["model"] == "nai-diffusion-4-5-full"
    assert colorize["prompt"] == "warm tones"
    assert colorize["defry"] == 5

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
    body = payload_builder.build_official_generation(
        make_settings(model="nai-diffusion-5-full"), spec, ()
    )

    assert body["model"] == "nai-diffusion-3"
    assert body["parameters"]["noise_schedule"] == "native"
    assert "v4_prompt" not in body["parameters"]


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
