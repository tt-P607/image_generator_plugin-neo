"""NovelAI 模型能力档案。

集中声明插件支持的模型、提示词能力、生成默认值与功能边界，供描述、
请求解析和 payload 构造共同使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModelFamily = Literal["v4.5", "v5"]
ModelEdition = Literal["full", "curated"]


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """单个 NovelAI 模型的能力与推荐默认值。"""

    model_id: str
    family: ModelFamily
    edition: ModelEdition
    inpainting: bool
    inpainting_model: str
    prompt_token_limit: int
    text_token_limit: int | None
    prompt_languages: tuple[str, ...]
    weight_range: tuple[float, float]
    default_steps: int
    default_guidance: float
    default_pgr: float
    default_variety_plus: bool
    default_sampler: str
    default_noise_schedule: str
    supports_vibe: bool
    supports_director_reference: bool
    supports_native_alpha: bool
    supports_control_tags: bool
    supports_visual_novel_assets: bool
    supports_comics: bool
    max_characters: int
    recommended_original_characters: int

    @property
    def is_v5(self) -> bool:
        """当前档案是否属于 V5。"""

        return self.family == "v5"


def _v45_profile(
    model_id: str,
    edition: ModelEdition,
    *,
    inpainting: bool = False,
) -> ModelProfile:
    """构造 V4.5 模型档案。"""

    return ModelProfile(
        model_id=model_id,
        family="v4.5",
        edition=edition,
        inpainting=inpainting,
        inpainting_model=f"nai-diffusion-4-5-{edition}-inpainting",
        prompt_token_limit=505,
        text_token_limit=None,
        prompt_languages=("en",),
        weight_range=(1.1, 1.4),
        default_steps=28,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="karras",
        supports_vibe=not inpainting,
        supports_director_reference=not inpainting,
        supports_native_alpha=False,
        supports_control_tags=False,
        supports_visual_novel_assets=False,
        supports_comics=False,
        max_characters=6,
        recommended_original_characters=6,
    )


def _v5_profile(
    model_id: str,
    edition: ModelEdition,
    *,
    inpainting: bool = False,
) -> ModelProfile:
    """构造 V5 模型档案。"""

    return ModelProfile(
        model_id=model_id,
        family="v5",
        edition=edition,
        inpainting=inpainting,
        inpainting_model="nai-diffusion-5-full-inpainting",
        prompt_token_limit=1471,
        text_token_limit=750,
        prompt_languages=("en", "ja", "zh-Hans", "zh-Hant"),
        weight_range=(1.3, 1.8),
        default_steps=28,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="karras",
        supports_vibe=False,
        supports_director_reference=False,
        supports_native_alpha=True,
        supports_control_tags=True,
        supports_visual_novel_assets=True,
        supports_comics=True,
        max_characters=22,
        recommended_original_characters=6,
    )


MODEL_PROFILES: dict[str, ModelProfile] = {
    profile.model_id: profile
    for profile in (
        _v45_profile("nai-diffusion-4-5-full", "full"),
        _v45_profile("nai-diffusion-4-5-curated", "curated"),
        _v45_profile(
            "nai-diffusion-4-5-full-inpainting",
            "full",
            inpainting=True,
        ),
        _v45_profile(
            "nai-diffusion-4-5-curated-inpainting",
            "curated",
            inpainting=True,
        ),
        _v5_profile("nai-diffusion-5-full", "full"),
        _v5_profile("nai-diffusion-5-curated", "curated"),
        _v5_profile(
            "nai-diffusion-5-full-inpainting",
            "full",
            inpainting=True,
        ),
    )
}

V45_MODELS: frozenset[str] = frozenset(
    model_id for model_id, profile in MODEL_PROFILES.items() if profile.family == "v4.5"
)
V5_MODELS: frozenset[str] = frozenset(
    model_id for model_id, profile in MODEL_PROFILES.items() if profile.family == "v5"
)
GENERATION_MODELS: frozenset[str] = frozenset(
    model_id for model_id, profile in MODEL_PROFILES.items() if not profile.inpainting
)


def get_model_profile(model_id: str) -> ModelProfile:
    """读取精确模型 ID 对应的能力档案。

    Args:
        model_id: NovelAI 精确模型 ID

    Returns:
        模型能力档案

    Raises:
        ValueError: 模型不受插件支持
    """

    cleaned = model_id.strip()
    try:
        return MODEL_PROFILES[cleaned]
    except KeyError as error:
        supported = ", ".join(sorted(GENERATION_MODELS))
        raise ValueError(
            f"不支持的 NovelAI 模型 {cleaned!r}，可用模型：{supported}"
        ) from error