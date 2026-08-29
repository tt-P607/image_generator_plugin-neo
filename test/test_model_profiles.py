"""NovelAI 模型能力档案测试。"""

from __future__ import annotations

import pytest

from image_generator_plugin_neo.engine.models import (
    GENERATION_MODELS,
    get_model_profile,
)


def test_v45_and_v5_profiles_expose_distinct_prompt_capabilities() -> None:
    """验证 V4.5 与 V5 的提示词能力不会互相串线。"""

    v45 = get_model_profile("nai-diffusion-4-5-full")
    v5 = get_model_profile("nai-diffusion-5-full")

    assert v45.prompt_token_limit == 505
    assert v45.prompt_languages == ("en",)
    assert v45.weight_range == (1.1, 1.4)
    assert v45.text_token_limit is None

    assert v5.prompt_token_limit == 1471
    assert "zh-Hans" in v5.prompt_languages
    assert v5.weight_range == (1.3, 1.8)
    assert v5.text_token_limit == 750


def test_v45_and_v5_profiles_expose_distinct_feature_boundaries() -> None:
    """验证参考图、Alpha、控制词和多角色边界按模型区分。"""

    v45 = get_model_profile("nai-diffusion-4-5-curated")
    v5 = get_model_profile("nai-diffusion-5-curated")

    assert v45.supports_vibe is True
    assert v45.supports_director_reference is True
    assert v45.supports_native_alpha is False
    assert v45.supports_control_tags is False
    assert v45.max_characters == 6

    assert v5.supports_vibe is False
    assert v5.supports_director_reference is False
    assert v5.supports_native_alpha is True
    assert v5.supports_control_tags is True
    assert v5.supports_visual_novel_assets is True
    assert v5.supports_comics is True
    assert v5.max_characters == 22
    assert v5.recommended_original_characters == 6


def test_generation_models_exclude_inpainting_variants() -> None:
    """验证 Bot 可选模型集合不暴露专用 Inpainting 模型。"""

    assert "nai-diffusion-5-full" in GENERATION_MODELS
    assert "nai-diffusion-5-full-inpainting" not in GENERATION_MODELS


def test_unknown_model_is_rejected() -> None:
    """验证未知模型不会再被误判为 V5。"""

    with pytest.raises(ValueError, match="不支持的 NovelAI 模型"):
        get_model_profile("nai-diffusion-future")