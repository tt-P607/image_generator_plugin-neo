"""NovelAI 请求体构造。

集中实现 official 与 gateway 两套协议的 payload 组装，全部为纯函数，
不触碰网络与文件系统，便于单元测试。
"""

from __future__ import annotations

import random
from typing import Any

from .models import get_model_profile
from .settings import EngineSettings
from .types import (
    CharacterPrompt,
    DirectorRefAsset,
    DirectorToolSpec,
    GenerationSpec,
    InpaintSpec,
    VibeAsset,
)

SEED_MAX = 999_999_999
VARIETY_PLUS_SIGMA = 58

# 网关统一图片端点：根据请求体字段（image/mask/extra）自动路由到对应功能。
GATEWAY_GENERATIONS_PATH = "/v1/images/generations"

# 导演工具 tool_type → extra 值映射
GATEWAY_DIRECTOR_EXTRAS: dict[str, str] = {
    "declutter": "director-declutter",
    "bg-removal": "director-bg-remover",
    "lineart": "director-lineart",
    "sketch": "director-sketch",
    "colorize": "director-colorize",
    "emotion": "director-emotion",
}

PROMPT_TOOLS = ("colorize", "emotion")

# uc_preset 整数（official 约定）→ gateway 字符串（OpenAI 兼容网关要求字符串枚举）
GATEWAY_UC_PRESETS: dict[int, str] = {
    0: "strong",
    1: "light",
    2: "furry_focus",
    3: "human_focus",
    4: "none",
}


def merge_negative_prompts(
    base: str,
    extra: str | None,
    *,
    render_text: bool = False,
) -> str:
    """合并全局负面词与本次额外负面词。

    保留全局词顺序，仅追加去重后的新词。

    Args:
        base: 全局负面提示词
        extra: 本次额外负面提示词
        render_text: 是否需要画面文字；为 True 时移除全局的 text 负面标签

    Returns:
        合并后的负面提示词
    """
    base_tags = [tag.strip() for tag in base.split(",") if tag.strip()]
    if render_text:
        base_tags = [tag for tag in base_tags if tag.lower() != "text"]
    if not extra:
        return ", ".join(base_tags)
    if not base_tags:
        return extra

    seen = {tag.lower() for tag in base_tags}
    extras = [
        tag.strip()
        for tag in extra.split(",")
        if tag.strip() and tag.strip().lower() not in seen
    ]
    return ", ".join(base_tags + extras)


def _base_parameters(
    settings: EngineSettings,
    *,
    width: int,
    height: int,
    scale: float | None,
    steps: int | None,
    seed: int,
    model: str | None = None,
) -> dict[str, Any]:
    """构造 official 协议中两类请求共享的基础参数。

    Args:
        settings: 引擎配置快照
        width: 目标宽度
        height: 目标高度
        scale: 引导比例覆盖
        steps: 采样步数覆盖
        seed: 随机种子
        model: 实际使用的模型名，None 时沿用 settings.model
    """
    effective = model or settings.model
    is_v4 = EngineSettings.check_is_v4_or_v5(effective)

    return {
        "width": width,
        "height": height,
        "scale": scale if scale is not None else settings.scale,
        "steps": steps if steps is not None else settings.steps,
        "sampler": settings.sampler,
        "seed": seed,
        "n_samples": 1,
        "ucPreset": settings.uc_preset,
        "qualityToggle": True,
        "sm": False,
        "sm_dyn": False,
        "noise_schedule": settings.noise_schedule if is_v4 else "native",
    }


def _v4_common_parameters(
    settings: EngineSettings,
    *,
    prompt: str,
    negative_prompt: str,
    cfg_rescale: float | None,
    variety_plus: bool | None,
    model: str | None = None,
) -> dict[str, Any]:
    """构造 V4 系列模型公共参数块。

    Args:
        settings: 引擎配置快照
        prompt: 正面提示词
        negative_prompt: 负面提示词
        cfg_rescale: CFG 缩放覆盖
        variety_plus: Variety+ 覆盖
        model: 实际使用的模型名，None 时沿用 settings.model
    """
    effective = model or settings.model
    vibes_supported = EngineSettings.check_supports_vibes(effective)

    effective_rescale = cfg_rescale if cfg_rescale is not None else settings.cfg_rescale
    effective_variety = (
        variety_plus if variety_plus is not None else settings.variety_plus
    )
    params: dict[str, Any] = {
        "params_version": 3,
        "cfg_rescale": effective_rescale,
        "autoSmea": False,
        "legacy": False,
        "legacy_v3_extend": False,
        "legacy_uc": False,
        "controlnet_strength": 1,
        "dynamic_thresholding": False,
        "prefer_brownian": True,
        "normalize_reference_strength_multiple": False,
        "use_coords": False,
        "deliberate_euler_ancestral_bug": False,
        "skip_cfg_above_sigma": VARIETY_PLUS_SIGMA if effective_variety else None,
        "characterPrompts": [],
        "v4_prompt": {
            "caption": {"base_caption": prompt, "char_captions": []},
            "use_coords": False,
            "use_order": True,
        },
        "v4_negative_prompt": {
            "caption": {"base_caption": negative_prompt, "char_captions": []},
            "legacy_uc": False,
        },
        "negative_prompt": negative_prompt,
    }
    if vibes_supported:
        params["reference_image_multiple"] = []
        params["reference_information_extracted_multiple"] = []
        params["reference_strength_multiple"] = []
    return params


def _apply_characters(
    parameters: dict[str, Any],
    characters: tuple[CharacterPrompt, ...],
    *,
    use_coords: bool,
) -> None:
    """将多人物信息写入 official 参数块。"""

    if not characters:
        return

    api_characters: list[dict[str, Any]] = []
    positive_captions: list[dict[str, Any]] = []
    negative_captions: list[dict[str, Any]] = []

    for character in characters:
        center = {
            "x": max(0.0, min(1.0, character.x)),
            "y": max(0.0, min(1.0, character.y)),
        }
        api_characters.append(
            {
                "prompt": character.prompt,
                "uc": character.negative_prompt,
                "center": center,
                "enabled": True,
            }
        )
        positive_captions.append(
            {"char_caption": character.prompt, "centers": [center]}
        )
        negative_captions.append(
            {"char_caption": character.negative_prompt, "centers": [center]}
        )

    parameters["characterPrompts"] = api_characters
    parameters["v4_prompt"]["caption"]["char_captions"] = positive_captions
    parameters["v4_negative_prompt"]["caption"]["char_captions"] = negative_captions
    parameters["use_coords"] = use_coords
    parameters["v4_prompt"]["use_coords"] = use_coords


def _apply_director_refs(
    parameters: dict[str, Any],
    refs: tuple[DirectorRefAsset, ...],
) -> None:
    """将精密参考写入 official 参数块。"""

    if not refs:
        return

    parameters["director_reference_images"] = [ref.data for ref in refs]
    parameters["director_reference_descriptions"] = [
        {
            "caption": {"base_caption": ref.ref_type, "char_captions": []},
            "legacy_uc": False,
        }
        for ref in refs
    ]
    parameters["director_reference_strength_values"] = [
        round(ref.strength, 2) for ref in refs
    ]
    parameters["director_reference_secondary_strength_values"] = [
        round(1.0 - ref.fidelity, 2) for ref in refs
    ]
    parameters["director_reference_information_extracted"] = [1.0 for _ in refs]


def _apply_vibes(parameters: dict[str, Any], vibes: tuple[VibeAsset, ...]) -> None:
    """将 Vibe 参考写入 official 参数块。"""

    if not vibes:
        return

    parameters["reference_image_multiple"] = [vibe.data for vibe in vibes]
    parameters["reference_information_extracted_multiple"] = [
        vibe.information_extracted for vibe in vibes
    ]
    parameters["reference_strength_multiple"] = [vibe.strength for vibe in vibes]


def build_official_generation(
    settings: EngineSettings,
    spec: GenerationSpec,
    vibes: tuple[VibeAsset, ...],
) -> dict[str, Any]:
    """构造 official 渠道文生图 / 图生图请求体。

    Args:
        settings: 引擎配置快照
        spec: 生图请求描述
        vibes: 本次需要注入的 Vibe，精密参考存在时应传空元组

    Returns:
        official API 请求体
    """
    effective_model = spec.model or settings.model
    is_v4 = EngineSettings.check_is_v4_or_v5(effective_model)
    vibes_ok = EngineSettings.check_supports_vibes(effective_model)
    refs_ok = EngineSettings.check_supports_director_refs(effective_model)

    negative_prompt = merge_negative_prompts(
        settings.negative_prompt,
        spec.negative_prompt,
        render_text=spec.render_text,
    )
    seed = random.randint(0, SEED_MAX)
    parameters = _base_parameters(
        settings,
        width=spec.width,
        height=spec.height,
        scale=spec.scale,
        steps=spec.steps,
        seed=seed,
        model=effective_model,
    )

    if is_v4:
        parameters.update(
            _v4_common_parameters(
                settings,
                prompt=spec.prompt,
                negative_prompt=negative_prompt,
                cfg_rescale=spec.cfg_rescale,
                variety_plus=spec.variety_plus,
                model=effective_model,
            )
        )
        parameters["add_original_image"] = False
        _apply_characters(
            parameters,
            spec.characters,
            use_coords=settings.always_use_coords,
        )
        if refs_ok:
            _apply_director_refs(parameters, spec.director_refs)
        if vibes_ok and not spec.is_img2img and not spec.director_refs:
            _apply_vibes(parameters, vibes)
    else:
        parameters["negative_prompt"] = negative_prompt

    payload: dict[str, Any] = {
        "input": spec.prompt,
        "model": effective_model,
        "action": "generate",
        "parameters": parameters,
    }

    if spec.is_img2img and spec.source_image:
        strength = (
            spec.strength
            if spec.strength is not None
            else settings.img2img_default_strength
        )
        payload["action"] = "img2img"
        parameters.update(
            {
                "image": spec.source_image,
                "strength": strength,
                "noise": 0.0,
                "extra_noise_seed": random.randint(0, SEED_MAX),
                "img2img": {"color_correct": True, "strength": strength},
                # infill/img2img 输出只含重绘区域，需叠回原图才能得到完整画面。
                "add_original_image": True,
                "inpaintImg2ImgStrength": strength,
            }
        )

    return payload


def build_official_inpaint(
    settings: EngineSettings,
    spec: InpaintSpec,
) -> dict[str, Any]:
    """构造 official 渠道局部重绘请求体。

    Args:
        settings: 引擎配置快照
        spec: 局部重绘请求描述

    Returns:
        official API 请求体
    """
    negative_prompt = merge_negative_prompts(
        settings.negative_prompt,
        spec.negative_prompt,
        render_text=spec.render_text,
    )
    effective_model = spec.model or settings.model
    profile = get_model_profile(effective_model)
    seed = random.randint(0, SEED_MAX)
    parameters = _base_parameters(
        settings,
        width=spec.width,
        height=spec.height,
        scale=spec.scale,
        steps=spec.steps,
        seed=seed,
        model=effective_model,
    )
    parameters.update(
        {
            "image": spec.source_image,
            "mask": spec.mask,
            "strength": spec.strength,
            "noise": 0,
            "extra_noise_seed": seed,
            "img2img": {"color_correct": True, "strength": 1.0},
            "inpaintImg2ImgStrength": spec.strength,
            # infill 模型只输出重绘区域，必须让 API 叠加原图补齐保留区域。
            "add_original_image": True,
        }
    )

    if EngineSettings.check_is_v4_or_v5(effective_model):
        parameters.update(
            _v4_common_parameters(
                settings,
                prompt=spec.prompt,
                negative_prompt=negative_prompt,
                cfg_rescale=spec.cfg_rescale,
                variety_plus=spec.variety_plus,
                model=effective_model,
            )
        )
        parameters["add_original_image"] = True
    else:
        parameters["negative_prompt"] = negative_prompt

    return {
        "input": spec.prompt,
        "model": profile.inpainting_model,
        "action": "infill",
        "parameters": parameters,
        "use_new_shared_trial": True,
    }


def build_official_director(
    spec: DirectorToolSpec,
) -> dict[str, Any]:
    """构造 official 渠道导演工具请求体。

    Args:
        spec: 导演工具请求描述

    Returns:
        official augment-image 请求体
    """
    payload: dict[str, Any] = {
        "req_type": spec.tool_type,
        "image": spec.source_image,
        "width": spec.width,
        "height": spec.height,
    }
    if spec.prompt and spec.tool_type in PROMPT_TOOLS:
        payload["prompt"] = spec.prompt
    if spec.defry is not None and spec.tool_type in PROMPT_TOOLS:
        payload["defry"] = max(0, min(5, spec.defry))
    return payload


def build_official_upscale(
    image_b64: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """构造 official 渠道 4x 放大请求体。

    Args:
        image_b64: 源图 base64
        width: 源图宽度
        height: 源图高度

    Returns:
        official upscale 请求体
    """
    return {
        "image": image_b64,
        "width": width,
        "height": height,
        "scale": 4,
    }


def build_gateway_upscale(
    image_b64: str,
    width: int,
    height: int,
    model: str,
) -> dict[str, Any]:
    """构造 Gateway 渠道 4x 放大请求体。

    统一走 ``/v1/images/generations`` 端点，通过 ``extra: "upscale"`` 触发放大。

    Args:
        image_b64: 源图 base64
        width: 源图宽度
        height: 源图高度
        model: 标准模型名

    Returns:
        Gateway generations（upscale）请求体
    """
    return {
        "model": model,
        "extra": "upscale",
        "image": image_b64,
        "width": width,
        "height": height,
        "response_format": "b64_json",
    }


def build_encode_vibe(
    settings: EngineSettings,
    image_b64: str,
    information_extracted: float,
    model: str | None = None,
) -> dict[str, Any]:
    """构造 Vibe 编码请求体。

    Gateway 渠道统一走 ``/v1/images/generations`` 端点，通过 ``extra: "encode-vibe"``
    触发编码；official 渠道走 ``/ai/encode-vibe``。请求体结构两渠道通用。

    Args:
        settings: 引擎配置快照
        image_b64: 原始图片 base64
        information_extracted: 信息提取量
        model: Vibe 向量所属模型，None 时使用默认模型

    Returns:
        encode-vibe 请求体
    """
    return {
        "image": image_b64,
        "information_extracted": information_extracted,
        "model": model or settings.model,
    }


def build_gateway_generation(
    settings: EngineSettings,
    spec: GenerationSpec,
    vibes: tuple[VibeAsset, ...] = (),
) -> dict[str, Any]:
    """构造 Gateway 渠道文生图 / 图生图 / Vibe 转移请求体。

    遵循新版 OpenAI 图片接口规范：顶层仅保留通用字段（model / prompt / n /
    size / image / strength），NovelAI 专属参数统一收入 ``params`` 对象，
    根据额外字段自动路由：提供 ``image`` 即为图生图。

    Args:
        settings: 引擎配置快照
        spec: 生图请求描述
        vibes: 已编码的 Vibe 列表，非空时在 params 注入参考图字段

    Returns:
        Gateway generations 请求体
    """
    effective_model = spec.model or settings.model
    refs_ok = EngineSettings.check_supports_director_refs(effective_model)
    vibes_ok = EngineSettings.check_supports_vibes(effective_model)

    params: dict[str, Any] = {
        "steps": spec.steps if spec.steps is not None else settings.steps,
        "scale": spec.scale if spec.scale is not None else settings.scale,
        "cfg_rescale": (
            spec.cfg_rescale if spec.cfg_rescale is not None else settings.cfg_rescale
        ),
        "sampler": settings.sampler,
        "noise_schedule": settings.noise_schedule,
        "negative_prompt": merge_negative_prompts(
            settings.negative_prompt,
            spec.negative_prompt,
            render_text=spec.render_text,
        ),
        "quality": True,
        "uc_preset": GATEWAY_UC_PRESETS[settings.uc_preset],
    }
    effective_variety = (
        spec.variety_plus
        if spec.variety_plus is not None
        else settings.variety_plus
    )
    if effective_variety:
        params["variety_boost"] = True

    payload: dict[str, Any] = {
        "model": effective_model,
        "prompt": spec.prompt,
        "n": 1,
        "size": f"{spec.width}x{spec.height}",
        "params": params,
    }

    # 图生图：顶层提供 image 即走 img2img
    if spec.is_img2img and spec.source_image:
        strength = (
            spec.strength
            if spec.strength is not None
            else settings.img2img_default_strength
        )
        payload["image"] = spec.source_image
        payload["strength"] = strength

    if spec.characters:
        params["characters"] = [
            {
                "prompt": character.prompt,
                "negative_prompt": character.negative_prompt,
                "position": [character.x, character.y],
                "enabled": True,
            }
            for character in spec.characters
        ]
        params["use_coords"] = bool(settings.always_use_coords)

    if refs_ok and spec.director_refs:
        params["character_references"] = [
            {
                "image": ref.data,
                "type": ref.ref_type,
                "strength": ref.strength,
                "fidelity": ref.fidelity,
                "information_extracted": 1.0,
            }
            for ref in spec.director_refs
        ]

    if vibes_ok and vibes:
        params["reference_image_multiple"] = [vibe.data for vibe in vibes]
        params["reference_strength_multiple"] = [vibe.strength for vibe in vibes]
        params["reference_information_extracted_multiple"] = [
            vibe.information_extracted for vibe in vibes
        ]

    return payload


def build_gateway_inpaint(
    settings: EngineSettings,
    spec: InpaintSpec,
) -> dict[str, Any]:
    """构造 Gateway 渠道局部重绘请求体。

    新版 OpenAI 兼容端点在文生图基础上顶层提供 ``image`` + ``mask`` 即走局部重绘；
    NovelAI 专属采样参数统一收入 ``params``。蒙版需符合 OpenAI 语义
    （透明区域重绘、不透明区域保留），由调用方（引擎）预先完成转换。

    Args:
        settings: 引擎配置快照
        spec: 局部重绘请求描述

    Returns:
        Gateway generations（inpainting）请求体
    """
    effective_model = spec.model or settings.model
    payload = {
        "model": effective_model,
        "prompt": spec.prompt,
        "size": f"{spec.width}x{spec.height}",
        "image": spec.source_image,
        "mask": spec.mask,
        "strength": spec.strength,
        "params": {
            "steps": spec.steps if spec.steps is not None else settings.steps,
            "scale": spec.scale if spec.scale is not None else settings.scale,
            "cfg_rescale": (
                spec.cfg_rescale
                if spec.cfg_rescale is not None
                else settings.cfg_rescale
            ),
            "sampler": settings.sampler,
            "noise_schedule": settings.noise_schedule,
            "negative_prompt": merge_negative_prompts(
                settings.negative_prompt,
                spec.negative_prompt,
                render_text=spec.render_text,
            ),
            "quality": True,
            "uc_preset": GATEWAY_UC_PRESETS[settings.uc_preset],
        },
    }
    effective_variety = (
        spec.variety_plus
        if spec.variety_plus is not None
        else settings.variety_plus
    )
    if effective_variety:
        payload["params"]["variety_boost"] = True
    return payload


def build_gateway_director(
    spec: DirectorToolSpec,
    model: str,
) -> dict[str, Any]:
    """构造 Gateway 渠道导演工具请求体。

    新版网关统一使用 ``/v1/images/generations`` 端点，通过 ``extra: "director-{tool}"``
    触发对应导演工具。

    Args:
        spec: 导演工具请求描述
        model: 标准模型名

    Returns:
        Gateway generations（director）请求体
    """
    extra = GATEWAY_DIRECTOR_EXTRAS.get(spec.tool_type)
    if extra is None:
        raise ValueError(f"未知的导演工具类型: {spec.tool_type}")

    payload: dict[str, Any] = {
        "model": model,
        "extra": extra,
        "image": spec.source_image,
        "width": spec.width,
        "height": spec.height,
        "response_format": "b64_json",
    }
    if spec.prompt and spec.tool_type in PROMPT_TOOLS:
        payload["prompt"] = spec.prompt
    if spec.defry is not None and spec.tool_type in PROMPT_TOOLS:
        payload["defry"] = max(0, min(5, spec.defry))
    return payload
