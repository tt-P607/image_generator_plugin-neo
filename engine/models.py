"""NovelAI 模型能力档案。

集中声明插件支持的模型、提示词能力、生成默认值与功能边界，供描述、
请求解析和 payload 构造共同使用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

ModelFamily = Literal["v3", "v4", "v4.5", "v5"]
ModelEdition = Literal["full", "curated", "anime", "furry"]

_ALL_NOISE_SCHEDULES = frozenset(
    {"native", "karras", "exponential", "polyexponential"}
)
_V4_NOISE_SCHEDULES = frozenset(
    {"karras", "exponential", "polyexponential"}
)
_DPM2_NOISE_SCHEDULES = frozenset({"exponential", "polyexponential"})
_NO_NOISE_SCHEDULES = frozenset()
_SAMPLER_NOISE_SCHEDULES: dict[ModelFamily, dict[str, frozenset[str]]] = {
    "v3": {
        "k_euler": _ALL_NOISE_SCHEDULES,
        "k_euler_ancestral": _ALL_NOISE_SCHEDULES,
        "k_dpm_2": _DPM2_NOISE_SCHEDULES,
        "k_dpm_2_ancestral": _NO_NOISE_SCHEDULES,
        "k_dpmpp_2m": _ALL_NOISE_SCHEDULES,
        "k_dpmpp_2m_sde": _ALL_NOISE_SCHEDULES,
        "k_dpmpp_2s_ancestral": _ALL_NOISE_SCHEDULES,
        "k_dpmpp_sde": _ALL_NOISE_SCHEDULES,
        "ddim": _NO_NOISE_SCHEDULES,
    },
    "v4": {
        "k_euler": _V4_NOISE_SCHEDULES,
        "k_euler_ancestral": _V4_NOISE_SCHEDULES,
        "k_dpm_2": _DPM2_NOISE_SCHEDULES,
        "k_dpm_2_ancestral": _NO_NOISE_SCHEDULES,
        "k_dpmpp_2m": _V4_NOISE_SCHEDULES,
        "k_dpmpp_2m_sde": _V4_NOISE_SCHEDULES,
        "k_dpmpp_2s_ancestral": _V4_NOISE_SCHEDULES,
        "k_dpmpp_sde": _V4_NOISE_SCHEDULES,
        "ddim": _NO_NOISE_SCHEDULES,
    },
    "v4.5": {
        "k_euler": _V4_NOISE_SCHEDULES,
        "k_euler_ancestral": _V4_NOISE_SCHEDULES,
        "k_dpm_2": _DPM2_NOISE_SCHEDULES,
        "k_dpm_2_ancestral": _NO_NOISE_SCHEDULES,
        "k_dpmpp_2m": _V4_NOISE_SCHEDULES,
        "k_dpmpp_2m_sde": _V4_NOISE_SCHEDULES,
        "k_dpmpp_2s_ancestral": _V4_NOISE_SCHEDULES,
        "k_dpmpp_sde": _V4_NOISE_SCHEDULES,
        "ddim": _NO_NOISE_SCHEDULES,
    },
    "v5": {
        "k_euler": frozenset({"karras"}),
        "k_euler_ancestral": frozenset({"karras"}),
        "k_dpm_2": frozenset({"karras"}),
        "k_dpm_2_ancestral": frozenset({"karras"}),
        "k_dpmpp_2m": frozenset({"karras"}),
        "k_dpmpp_2m_sde": frozenset({"karras"}),
        "k_dpmpp_2s_ancestral": frozenset({"karras"}),
        "k_dpmpp_sde": frozenset({"karras"}),
        "ddim": _NO_NOISE_SCHEDULES,
    },
}


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
    max_steps: int
    max_pixels: int
    params_version: int
    default_guidance: float
    default_pgr: float
    default_variety_plus: bool
    default_sampler: str
    default_noise_schedule: str | None
    supports_noise_schedule: bool
    supports_variety_plus: bool
    supports_vibe: bool
    supports_encoded_vibe: bool
    vibe_encoding_key: str | None
    supports_director_reference: bool
    supports_enhance_prompt_add: bool
    supports_max_enhance: bool
    supports_native_alpha: bool
    supports_control_tags: bool
    supports_visual_novel_assets: bool
    supports_comics: bool
    supports_characters: bool
    supports_freeform_character_position: bool
    max_characters: int
    recommended_original_characters: int

    @property
    def is_v5(self) -> bool:
        """当前档案是否属于 V5。"""

        return self.family == "v5"

    def allowed_noise_schedules(self, sampler: str) -> frozenset[str]:
        """返回指定采样器可用的噪声调度集合。"""

        return _SAMPLER_NOISE_SCHEDULES[self.family].get(
            sampler,
            _NO_NOISE_SCHEDULES,
        )

    def supports_sampler(self, sampler: str) -> bool:
        """判断模型是否认识指定采样器。"""

        return sampler in _SAMPLER_NOISE_SCHEDULES[self.family]

    @property
    def supported_samplers(self) -> tuple[str, ...]:
        """返回当前模型按官网顺序支持的采样器。"""

        return tuple(_SAMPLER_NOISE_SCHEDULES[self.family])

    def normalize_character_coordinate(self, value: float) -> float:
        """按模型能力归一化单个角色坐标。"""

        clamped = max(0.0, min(1.0, value))
        if self.supports_freeform_character_position:
            return clamped
        centers = (0.1, 0.3, 0.5, 0.7, 0.9)
        return centers[min(int(clamped * 5), 4)]


def _v3_profile(
    model_id: str,
    edition: ModelEdition,
    *,
    inpainting: bool = False,
) -> ModelProfile:
    """构造 V3 模型档案。"""

    return ModelProfile(
        model_id=model_id,
        family="v3",
        edition=edition,
        inpainting=inpainting,
        inpainting_model="nai-diffusion-3-inpainting",
        prompt_token_limit=225,
        text_token_limit=None,
        prompt_languages=("en",),
        weight_range=(1.0, 1.3),
        default_steps=28,
        max_steps=50,
        max_pixels=3_145_728,
        params_version=4,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="native",
        supports_noise_schedule=True,
        supports_variety_plus=True,
        supports_vibe=not inpainting,
        supports_encoded_vibe=False,
        vibe_encoding_key=None,
        supports_director_reference=False,
        supports_enhance_prompt_add=False,
        supports_max_enhance=False,
        supports_native_alpha=False,
        supports_control_tags=False,
        supports_visual_novel_assets=False,
        supports_comics=False,
        supports_characters=False,
        supports_freeform_character_position=False,
        max_characters=0,
        recommended_original_characters=0,
    )


def _v4_profile(
    model_id: str,
    edition: ModelEdition,
    *,
    inpainting: bool = False,
) -> ModelProfile:
    """构造 V4 模型档案。"""

    return ModelProfile(
        model_id=model_id,
        family="v4",
        edition=edition,
        inpainting=inpainting,
        inpainting_model=f"nai-diffusion-4-{edition}-inpainting",
        prompt_token_limit=505,
        text_token_limit=None,
        prompt_languages=("en",),
        weight_range=(1.1, 1.4),
        default_steps=28,
        max_steps=50,
        max_pixels=3_145_728,
        params_version=4,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="karras",
        supports_noise_schedule=True,
        supports_variety_plus=True,
        supports_vibe=not inpainting,
        supports_encoded_vibe=not inpainting,
        vibe_encoding_key=(
            "v4curated" if edition == "curated" else "v4full"
        ),
        supports_director_reference=False,
        supports_enhance_prompt_add=False,
        supports_max_enhance=False,
        supports_native_alpha=False,
        supports_control_tags=False,
        supports_visual_novel_assets=False,
        supports_comics=False,
        supports_characters=True,
        supports_freeform_character_position=False,
        max_characters=6,
        recommended_original_characters=6,
    )


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
        max_steps=50,
        max_pixels=3_145_728,
        params_version=4,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="karras",
        supports_noise_schedule=True,
        supports_variety_plus=True,
        supports_vibe=not inpainting,
        supports_encoded_vibe=not inpainting,
        vibe_encoding_key=(
            "v4-5curated" if edition == "curated" else "v4-5full"
        ),
        supports_director_reference=True,
        supports_enhance_prompt_add=True,
        supports_max_enhance=False,
        supports_native_alpha=False,
        supports_control_tags=False,
        supports_visual_novel_assets=False,
        supports_comics=False,
        supports_characters=True,
        supports_freeform_character_position=False,
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
        max_steps=28,
        max_pixels=1_048_576,
        params_version=4,
        default_guidance=5.0,
        default_pgr=0.0,
        default_variety_plus=False,
        default_sampler="k_euler_ancestral",
        default_noise_schedule="karras",
        supports_noise_schedule=False,
        supports_variety_plus=False,
        supports_vibe=False,
        supports_encoded_vibe=False,
        vibe_encoding_key=None,
        supports_director_reference=False,
        supports_enhance_prompt_add=True,
        supports_max_enhance=True,
        supports_native_alpha=True,
        supports_control_tags=True,
        supports_visual_novel_assets=True,
        supports_comics=True,
        supports_characters=True,
        supports_freeform_character_position=True,
        max_characters=32,
        recommended_original_characters=6,
    )


MODEL_PROFILES: dict[str, ModelProfile] = {
    profile.model_id: profile
    for profile in (
        # V5 模型（公开计价商品）
        _v5_profile("nai-diffusion-5-full", "full"),
        _v5_profile("nai-diffusion-5-curated", "curated"),
        _v5_profile(
            "nai-diffusion-5-full-inpainting",
            "full",
            inpainting=True,
        ),
        # V4.5 模型（公开计价商品）
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
        # V4 模型（公开 ID）
        _v4_profile("nai-diffusion-4-full", "full"),
        _v4_profile("nai-diffusion-4-curated-preview", "curated"),
        _v4_profile(
            "nai-diffusion-4-full-inpainting",
            "full",
            inpainting=True,
        ),
        _v4_profile(
            "nai-diffusion-4-curated-inpainting",
            "curated",
            inpainting=True,
        ),
        # V3 模型（公开 ID）
        _v3_profile("nai-diffusion-3", "anime"),
        _v3_profile("nai-diffusion-furry-3", "furry"),
        _v3_profile(
            "nai-diffusion-3-inpainting",
            "anime",
            inpainting=True,
        ),
    )
}

V3_MODELS: frozenset[str] = frozenset(
    model_id for model_id, profile in MODEL_PROFILES.items() if profile.family == "v3"
)
V4_MODELS: frozenset[str] = frozenset(
    model_id for model_id, profile in MODEL_PROFILES.items() if profile.family == "v4"
)
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


def infer_model_profile(model_name: str) -> ModelProfile | None:
    """按名称关键词推断第三方模型名对应的能力档案。

    识别规则（大小写不敏感）：
    - 代际：
      - ``4.5`` / ``4-5`` / ``45`` / ``v4.5`` 判为 V4.5。
      - ``5`` / ``v5`` 判为 V5。
      - ``4`` / ``v4`` 判为 V4。
      - ``3`` / ``v3`` / ``furry`` 判为 V3。
      多规则匹配时按特定性排序（4.5 优先于 4 和 5）。
    - 画风：``curated`` 判为 Curated；若为 V3 且含 ``furry`` 判为 Furry，否则默认 Full / Anime。
    - 推断结果复用对应官方模型的能力档案，仅 ``model_id`` 替换为实际名称。

    Args:
        model_name: 任意来源的模型名（官方 ID 或第三方自定义名）

    Returns:
        推断出的能力档案；无法识别代际时返回 None
    """

    lowered = model_name.strip().lower()
    if not lowered:
        return None

    if "4.5" in lowered or "4-5" in lowered or "45" in lowered:
        family: ModelFamily = "v4.5"
    elif "5" in lowered or "v5" in lowered:
        family = "v5"
    elif "4" in lowered or "v4" in lowered:
        family = "v4"
    elif "3" in lowered or "v3" in lowered or "furry" in lowered:
        family = "v3"
    else:
        return None

    if family == "v3":
        if "furry" in lowered:
            base_id = "nai-diffusion-furry-3"
        else:
            base_id = "nai-diffusion-3"
    elif family == "v4":
        edition: ModelEdition = "curated" if "curated" in lowered else "full"
        base_id = "nai-diffusion-4-curated-preview" if edition == "curated" else "nai-diffusion-4-full"
    else:
        edition = "curated" if "curated" in lowered else "full"
        family_id = "4-5" if family == "v4.5" else "5"
        base_id = f"nai-diffusion-{family_id}-{edition}"

    profile = get_model_profile(base_id)
    return replace(profile, model_id=model_name.strip())


def resolve_model_profile(
    model_name: str,
    aliases: dict[str, str] | None = None,
) -> ModelProfile:
    """解析任意模型名为能力档案。

    解析顺序：官方精确 ID → 别名映射 → 关键词推断。

    Args:
        model_name: 模型名（官方 ID、别名或第三方自定义名）
        aliases: 别名映射（自定义名 → 官方模型 ID），可选

    Returns:
        模型能力档案

    Raises:
        ValueError: 无法识别模型代际
    """

    cleaned = model_name.strip()
    if cleaned in MODEL_PROFILES:
        return MODEL_PROFILES[cleaned]

    if aliases:
        target = aliases.get(cleaned)
        if target and target in MODEL_PROFILES:
            return MODEL_PROFILES[target]

    inferred = infer_model_profile(cleaned)
    if inferred is not None:
        return inferred

    supported = ", ".join(sorted(GENERATION_MODELS))
    raise ValueError(
        f"无法识别模型 {cleaned!r} 的代际。官方模型：{supported}；"
        "第三方模型名需包含 4.5/5 等版本关键词，或通过 model_aliases 显式映射"
    )