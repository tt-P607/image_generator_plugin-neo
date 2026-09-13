"""NovelAI V5 及集中式模型能力矩阵真实请求序列化回归测试。

覆盖两份权威文档的核心约束：
1. 《OpenAI兼容接口对接文档.md》
2. 《官方原生接口对接文档.md》

核心验证点：
- V5 在双渠道中始终发送 noise_schedule="karras"
- V5 即使配置中存在其他用户偏好，最终序列化的 JSON 仍归一为 karras
- 支持 noise_schedule 的旧模型（V4.5、V4、V3）按 sampler 矩阵发送合法调度
- V3 的 ddim 保留且不发送调度；V4/V4.5/V5 改写为默认采样器
- 模型在旧模型与 V5 之间动态切换时，V5 固定 karras，旧模型恢复用户偏好
- V5 即使开启 variety_plus，也绝对不发送 skip_cfg_above_sigma 与 variety_boost
- 全部模型 profile 的 params_version 均为 4
- V5 绝对不注入 Vibe 与角色参考图等不兼容字段
- 模型步数上限校验（V5 为 28，V4.5 为 50）
- 模型像素上限校验（V5 为 1,048,576）
- 模型角色上限校验（V5 为 32，V4.5 为 6，V3 为 0）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.engine.engine import ImageEngine
from image_generator_plugin_neo.engine.models import get_model_profile
from image_generator_plugin_neo.engine import payload as payload_builder
from image_generator_plugin_neo.engine.settings import EngineSettings
from image_generator_plugin_neo.engine.types import (
    CharacterPrompt,
    DirectorRefAsset,
    GenerationSpec,
    InpaintSpec,
    VibeAsset,
)


def make_settings(**overrides: object) -> EngineSettings:
    """构造引擎配置快照，可覆盖个别字段。"""

    config = ImageGeneratorConfig()
    settings = EngineSettings.from_config(config)
    if not overrides:
        return settings
    values = {field: getattr(settings, field) for field in settings.__slots__}
    values.update(overrides)
    return EngineSettings(**values)  # type: ignore[arg-type]


# ── 1. V5 noise_schedule 固定值测试（官方原生与 OpenAI 兼容双渠道） ──


@pytest.mark.parametrize(
    ("model_id", "configured_schedule"),
    [
        ("nai-diffusion-5-full", None),
        ("nai-diffusion-5-curated", "karras"),
        ("nai-diffusion-5-full", "exponential"),
        ("nai-diffusion-5-curated", "polyexponential"),
        ("nai-diffusion-5-full", "native"),
    ],
)
def test_v5_official_generation_uses_fixed_karras(
    model_id: str,
    configured_schedule: str | None,
) -> None:
    """验证 V5 官方原生文生图始终发送固定的 karras 调度。"""

    settings = make_settings(model=model_id, noise_schedule=configured_schedule)
    spec = GenerationSpec(prompt="1girl, blue hair", user_id="tester", model=model_id)

    payload = payload_builder.build_official_generation(settings, spec, ())
    raw_json = json.dumps(payload)
    parsed = json.loads(raw_json)

    parameters = parsed["parameters"]
    assert parameters["noise_schedule"] == "karras"
    assert '"noise_schedule": "karras"' in raw_json
    assert parameters["params_version"] == 4


@pytest.mark.parametrize(
    ("model_id", "configured_schedule"),
    [
        ("nai-diffusion-5-full", None),
        ("nai-diffusion-5-curated", "karras"),
        ("nai-diffusion-5-full", "exponential"),
        ("nai-diffusion-5-curated", "polyexponential"),
        ("nai-diffusion-5-full", "native"),
    ],
)
def test_v5_gateway_generation_uses_fixed_karras(
    model_id: str,
    configured_schedule: str | None,
) -> None:
    """验证 V5 Gateway 文生图始终在 params 中发送固定的 karras 调度。"""

    settings = make_settings(model=model_id, noise_schedule=configured_schedule)
    spec = GenerationSpec(prompt="1girl, blue hair", user_id="tester", model=model_id)

    payload = payload_builder.build_gateway_generation(settings, spec)
    raw_json = json.dumps(payload)
    parsed = json.loads(raw_json)

    params = parsed["params"]
    assert params["noise_schedule"] == "karras"
    assert '"noise_schedule": "karras"' in raw_json


def test_v5_official_inpaint_payload_uses_fixed_karras() -> None:
    """验证 V5 官方原生局部重绘始终发送固定的 karras 调度。"""

    settings = make_settings(model="nai-diffusion-5-full", noise_schedule="karras")
    spec = InpaintSpec(
        prompt="1girl",
        source_image="b64source",
        mask="b64mask",
        width=1024,
        height=1024,
        strength=0.7,
        model="nai-diffusion-5-full",
    )

    payload = payload_builder.build_official_inpaint(settings, spec)
    raw_json = json.dumps(payload)
    parsed = json.loads(raw_json)

    parameters = parsed["parameters"]
    assert parameters["noise_schedule"] == "karras"
    assert '"noise_schedule": "karras"' in raw_json
    assert parameters["params_version"] == 4


def test_v5_gateway_inpaint_payload_uses_fixed_karras() -> None:
    """验证 V5 Gateway 局部重绘始终在 params 中发送固定的 karras 调度。"""

    settings = make_settings(model="nai-diffusion-5-full", noise_schedule="karras")
    spec = InpaintSpec(
        prompt="1girl",
        source_image="b64source",
        mask="b64mask",
        width=1024,
        height=1024,
        strength=0.7,
        model="nai-diffusion-5-full",
    )

    payload = payload_builder.build_gateway_inpaint(settings, spec)
    raw_json = json.dumps(payload)
    parsed = json.loads(raw_json)

    params = parsed["params"]
    assert params["noise_schedule"] == "karras"
    assert '"noise_schedule": "karras"' in raw_json


# ── 2. 旧模型合法保留 noise_schedule ──


@pytest.mark.parametrize(
    ("model_id", "expected_schedule"),
    [
        ("nai-diffusion-4-5-full", "karras"),
        ("nai-diffusion-4-5-curated", "exponential"),
        ("nai-diffusion-4-full", "karras"),
        ("nai-diffusion-4-curated-preview", "polyexponential"),
        ("nai-diffusion-3", "native"),
        ("nai-diffusion-furry-3", "karras"),
    ],
)
def test_legacy_models_send_valid_noise_schedule(model_id: str, expected_schedule: str) -> None:
    """验证支持噪声调度的旧模型（V4.5、V4、V3）双渠道均正确发送 noise_schedule。"""

    settings = make_settings(model=model_id, noise_schedule=expected_schedule)
    spec = GenerationSpec(prompt="1girl", user_id="tester", model=model_id)

    official = payload_builder.build_official_generation(settings, spec, ())
    assert official["parameters"]["noise_schedule"] == expected_schedule
    assert official["parameters"]["params_version"] == 4

    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert gateway["params"]["noise_schedule"] == expected_schedule
    assert "params_version" not in gateway["params"]


@pytest.mark.parametrize(
    ("model_id", "sampler", "expected_schedules"),
    [
        (
            "nai-diffusion-3",
            "k_euler",
            frozenset({"native", "karras", "exponential", "polyexponential"}),
        ),
        (
            "nai-diffusion-4-full",
            "k_euler",
            frozenset({"karras", "exponential", "polyexponential"}),
        ),
        (
            "nai-diffusion-4-5-full",
            "k_dpm_2",
            frozenset({"exponential", "polyexponential"}),
        ),
        ("nai-diffusion-3", "k_dpm_2_ancestral", frozenset()),
        ("nai-diffusion-4-full", "k_dpm_2_ancestral", frozenset()),
        ("nai-diffusion-4-5-full", "k_dpm_2_ancestral", frozenset()),
        ("nai-diffusion-5-full", "k_euler", frozenset({"karras"})),
        ("nai-diffusion-5-full", "ddim", frozenset()),
    ],
)
def test_sampler_noise_schedule_matrix(
    model_id: str,
    sampler: str,
    expected_schedules: frozenset[str],
) -> None:
    """验证噪声调度选项同时受模型代际和采样器限制。"""

    profile = get_model_profile(model_id)
    assert profile.allowed_noise_schedules(sampler) == expected_schedules


@pytest.mark.parametrize(
    ("model_id", "sampler", "noise_schedule"),
    [
        ("nai-diffusion-4-5-full", "k_dpm_2", "karras"),
        ("nai-diffusion-4-full", "k_euler", "native"),
        ("nai-diffusion-3", "k_dpm_2", "karras"),
    ],
)
def test_invalid_legacy_sampler_schedule_is_rejected(
    model_id: str,
    sampler: str,
    noise_schedule: str,
) -> None:
    """验证旧模型的非法 sampler/schedule 组合不会被静默透传。"""

    settings = make_settings(
        model=model_id,
        sampler=sampler,
        noise_schedule=noise_schedule,
    )
    with pytest.raises(ValueError, match="不支持噪声调度"):
        settings.resolve_sampling_parameters(model_id)


def test_v3_ddim_is_preserved_without_noise_schedule() -> None:
    """验证 V3 使用 ddim 时保留采样器并省略 noise_schedule。"""

    settings = make_settings(
        model="nai-diffusion-3",
        sampler="ddim",
        noise_schedule="karras",
    )
    spec = GenerationSpec(prompt="1girl", user_id="tester", model="nai-diffusion-3")

    sampler, schedule = settings.resolve_sampling_parameters("nai-diffusion-3")
    assert sampler == "ddim"
    assert schedule is None

    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert official["parameters"]["sampler"] == "ddim"
    assert gateway["params"]["sampler"] == "ddim"
    assert "noise_schedule" not in official["parameters"]
    assert "noise_schedule" not in gateway["params"]


@pytest.mark.parametrize(
    "model_id",
    ["nai-diffusion-4-full", "nai-diffusion-4-5-full"],
)
def test_v4_and_v45_ddim_fall_back_without_noise_schedule(model_id: str) -> None:
    """验证 V4/V4.5 的旧 DDIM 配置改写采样器但仍省略调度器。"""

    settings = make_settings(
        model=model_id,
        sampler="ddim",
        noise_schedule="karras",
    )
    spec = GenerationSpec(prompt="1girl", user_id="tester", model=model_id)

    assert settings.resolve_sampling_parameters(model_id) == (
        "k_euler_ancestral",
        None,
    )
    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert official["parameters"]["sampler"] == "k_euler_ancestral"
    assert gateway["params"]["sampler"] == "k_euler_ancestral"
    assert "noise_schedule" not in official["parameters"]
    assert "noise_schedule" not in gateway["params"]


def test_v5_ddim_falls_back_to_default_sampler_and_fixed_karras() -> None:
    """验证 V5 的 ddim 配置改用受支持采样器并固定发送 karras。"""

    settings = make_settings(
        model="nai-diffusion-5-full",
        sampler="ddim",
        noise_schedule="exponential",
    )
    spec = GenerationSpec(prompt="1girl", user_id="tester", model=settings.model)

    assert settings.resolve_sampling_parameters() == (
        "k_euler_ancestral",
        "karras",
    )
    official = payload_builder.build_official_generation(settings, spec, ())
    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert official["parameters"]["sampler"] == "k_euler_ancestral"
    assert gateway["params"]["sampler"] == "k_euler_ancestral"
    assert official["parameters"]["noise_schedule"] == "karras"
    assert gateway["params"]["noise_schedule"] == "karras"


# ── 3. 模型动态切换安全测试（保留用户偏好，最终请求依当前模型能力过滤） ──


def test_model_switching_filters_and_restores_noise_schedule() -> None:
    """验证从 V4.5 切到 V5 时固定 karras，切回时恢复用户偏好。"""

    settings = make_settings(
        model="nai-diffusion-4-5-full",
        noise_schedule="exponential",
        available_models=(
            "nai-diffusion-4-5-full",
            "nai-diffusion-5-full",
        ),
    )

    # 阶段 1：使用 V4.5，正常发送 exponential
    spec_v45 = GenerationSpec(prompt="1girl", user_id="tester", model="nai-diffusion-4-5-full")
    official_v45 = payload_builder.build_official_generation(settings, spec_v45, ())
    gateway_v45 = payload_builder.build_gateway_generation(settings, spec_v45)
    assert official_v45["parameters"]["noise_schedule"] == "exponential"
    assert gateway_v45["params"]["noise_schedule"] == "exponential"

    # 阶段 2：切换到 V5，请求中固定发送 karras
    spec_v5 = GenerationSpec(prompt="1girl", user_id="tester", model="nai-diffusion-5-full")
    official_v5 = payload_builder.build_official_generation(settings, spec_v5, ())
    gateway_v5 = payload_builder.build_gateway_generation(settings, spec_v5)
    assert official_v5["parameters"]["noise_schedule"] == "karras"
    assert gateway_v5["params"]["noise_schedule"] == "karras"
    # settings 本身保留用户偏好不变
    assert settings.noise_schedule == "exponential"

    # 阶段 3：再切回 V4.5，恢复发送用户配置的 exponential
    spec_back = GenerationSpec(prompt="1girl", user_id="tester", model="nai-diffusion-4-5-full")
    official_back = payload_builder.build_official_generation(settings, spec_back, ())
    gateway_back = payload_builder.build_gateway_generation(settings, spec_back)
    assert official_back["parameters"]["noise_schedule"] == "exponential"
    assert gateway_back["params"]["noise_schedule"] == "exponential"


# ── 4. Variety+ 约束测试（V5 不支持，旧模型支持） ──


def test_v5_omits_variety_plus_fields_even_when_explicitly_requested() -> None:
    """验证 V5 即使显式设置 variety_plus=True，也绝不发送 skip_cfg_above_sigma 与 variety_boost。"""

    settings = make_settings(model="nai-diffusion-5-full", variety_plus=True)
    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        variety_plus=True,
        model="nai-diffusion-5-full",
    )

    official = payload_builder.build_official_generation(settings, spec, ())
    assert "skip_cfg_above_sigma" not in official["parameters"]

    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert "variety_boost" not in gateway["params"]


def test_v45_includes_variety_plus_when_enabled() -> None:
    """验证 V4.5 在启用 variety_plus 时正常发送相应字段。"""

    settings = make_settings(model="nai-diffusion-4-5-full", variety_plus=True)
    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        variety_plus=True,
        model="nai-diffusion-4-5-full",
    )

    official = payload_builder.build_official_generation(settings, spec, ())
    assert official["parameters"]["skip_cfg_above_sigma"] == 58

    gateway = payload_builder.build_gateway_generation(settings, spec)
    assert gateway["params"]["variety_boost"] is True


# ── 5. V5 不混入 Vibe 与角色参考图等不支持字段 ──


def test_v5_omits_vibe_and_director_reference_fields() -> None:
    """验证 V5 官方请求体中不包含 reference_image_multiple、director_reference 等 Vibe/参考字段。"""

    settings = make_settings(model="nai-diffusion-5-full")
    spec = GenerationSpec(
        prompt="1girl",
        user_id="tester",
        model="nai-diffusion-5-full",
        director_refs=(
            DirectorRefAsset(
                data="b64ref",
                ref_type="character&style",
                fidelity=1.0,
                strength=1.0,
            ),
        ),
    )
    vibe = VibeAsset(name="test", data="b64vibe", information_extracted=1.0, strength=0.6)

    official = payload_builder.build_official_generation(settings, spec, (vibe,))
    params = official["parameters"]
    assert "reference_image_multiple" not in params
    assert "controlnet_strength" not in params
    assert "director_reference_images" not in params

    gateway = payload_builder.build_gateway_generation(settings, spec, (vibe,))
    assert "reference_image_multiple" not in gateway["params"]
    assert "character_references" not in gateway["params"]


# ── 6. 步数与像素上限审计 ──


@pytest.mark.asyncio
async def test_v5_steps_upper_bound_validation() -> None:
    """验证 V5 步数超过 28 时被拦截，28 及以内允许。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-full"
    config.generation.available_models = ["nai-diffusion-5-full"]
    engine = ImageEngine(config)
    engine._submit = AsyncMock(return_value=object())  # type: ignore[method-assign]

    # steps = 28 合法
    await engine.generate(
        GenerationSpec(prompt="1girl", user_id="tester", steps=28)
    )
    engine._submit.assert_awaited_once()

    # steps = 29 超标
    fail_result = await engine.generate(
        GenerationSpec(prompt="1girl", user_id="tester", steps=29)
    )
    assert fail_result.success is False
    assert "采样步数必须在 1~28 之间" in fail_result.message


@pytest.mark.asyncio
async def test_v5_pixel_area_upper_bound_validation() -> None:
    """验证 V5 总像素超过 1,048,576 时被拦截。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-full"
    config.generation.available_models = ["nai-diffusion-5-full"]
    engine = ImageEngine(config)

    # 1216x1024 = 1,245,184 > 1,048,576
    result = await engine.generate(
        GenerationSpec(
            prompt="1girl",
            user_id="tester",
            width=1216,
            height=1024,
        )
    )
    assert result.success is False
    assert "总像素不能超过 1048576" in result.message


# ── 7. 普通角色上限校验 ──


@pytest.mark.asyncio
async def test_character_limits_per_model_family() -> None:
    """验证各代际角色上限：V5 最多 32 个，V4.5 最多 6 个，V3 不支持。"""

    config = ImageGeneratorConfig()
    config.generation.model = "nai-diffusion-5-full"
    config.generation.available_models = [
        "nai-diffusion-5-full",
        "nai-diffusion-4-5-full",
        "nai-diffusion-3",
    ]
    engine = ImageEngine(config)
    engine._submit = AsyncMock(return_value=object())  # type: ignore[method-assign]

    # V5 传入 32 个角色：合法
    chars_32 = tuple(CharacterPrompt(prompt=f"char{i}") for i in range(32))
    await engine.generate(GenerationSpec(prompt="group", user_id="tester", characters=chars_32))
    engine._submit.assert_awaited_once()

    # V5 传入 33 个角色：超限
    chars_33 = tuple(CharacterPrompt(prompt=f"char{i}") for i in range(33))
    r5 = await engine.generate(GenerationSpec(prompt="group", user_id="tester", characters=chars_33))
    assert r5.success is False
    assert "超过上限 32" in r5.message

    # V4.5 传入 7 个角色：超限
    chars_7 = tuple(CharacterPrompt(prompt=f"char{i}") for i in range(7))
    r45 = await engine.generate(
        GenerationSpec(
            prompt="group",
            user_id="tester",
            model="nai-diffusion-4-5-full",
            characters=chars_7,
        )
    )
    assert r45.success is False
    assert "超过上限 6" in r45.message

    # V3 传入角色：不支持
    r3 = await engine.generate(
        GenerationSpec(
            prompt="group",
            user_id="tester",
            model="nai-diffusion-3",
            characters=(CharacterPrompt(prompt="char0"),),
        )
    )
    assert r3.success is False
    assert "不支持多角色" in r3.message


# ── 8. 文档收录的全部 8 个公开模型档案完整性 ──


def test_all_documented_models_are_present_and_consistent() -> None:
    """验证文档列出的 8 个公开模型均在能力矩阵中且属性完整。"""

    expected_models = [
        "nai-diffusion-5-full",
        "nai-diffusion-5-curated",
        "nai-diffusion-4-5-full",
        "nai-diffusion-4-5-curated",
        "nai-diffusion-4-full",
        "nai-diffusion-4-curated-preview",
        "nai-diffusion-3",
        "nai-diffusion-furry-3",
    ]

    for model_id in expected_models:
        profile = get_model_profile(model_id)
        assert profile.model_id == model_id
        assert profile.params_version == 4
        if profile.is_v5:
            assert profile.supports_noise_schedule is False
            assert profile.supports_variety_plus is False
            assert profile.supports_vibe is False
            assert profile.supports_director_reference is False
            assert profile.max_steps == 28
            assert profile.max_characters == 32
        elif profile.family in ("v4", "v4.5", "v3"):
            assert profile.supports_noise_schedule is True
            assert profile.supports_variety_plus is True
            assert profile.max_steps == 50
