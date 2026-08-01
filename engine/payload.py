"""NovelAI 请求体构造。

集中实现 official 与 gateway 两套协议的 payload 组装，全部为纯函数，
不触碰网络与文件系统，便于单元测试。
"""

from __future__ import annotations

import random
from typing import Any

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

GATEWAY_GENERATIONS_PATH = "/v1/images/generations"
GATEWAY_IMG2IMG_PATH = "/v1/images/img2img"
GATEWAY_INPAINTING_PATH = "/v1/images/inpainting"
GATEWAY_VIBE_TRANSFER_PATH = "/v1/images/vibe-transfer"
GATEWAY_ENCODE_VIBE_PATH = "/v1/images/encode-vibe"
GATEWAY_UPSCALE_PATH = "/v1/images/upscale"
GATEWAY_DIRECTOR_PATHS: dict[str, str] = {
    "declutter": "/v1/images/director-declutter",
    "bg-removal": "/v1/images/director-bg-remover",
    "lineart": "/v1/images/director-lineart",
    "sketch": "/v1/images/director-sketch",
    "colorize": "/v1/images/director-colorize",
    "emotion": "/v1/images/director-emotion",
}

PROMPT_TOOLS = ("colorize", "emotion")


def merge_negative_prompts(base: str, extra: str | None) -> str:
    """合并全局负面词与本次额外负面词。

    保留全局词顺序，仅追加去重后的新词。

    Args:
        base: 全局负面提示词
        extra: 本次额外负面提示词

    Returns:
        合并后的负面提示词
    """
    if not extra:
        return base
    if not base:
        return extra

    base_tags = [tag.strip() for tag in base.split(",") if tag.strip()]
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
    seed: int,
) -> dict[str, Any]:
    """构造 official 协议中两类请求共享的基础参数。"""

    return {
        "width": width,
        "height": height,
        "scale": scale if scale is not None else settings.scale,
        "steps": settings.steps,
        "sampler": settings.sampler,
        "seed": seed,
        "n_samples": 1,
        "ucPreset": settings.uc_preset,
        "qualityToggle": True,
        "sm": False,
        "sm_dyn": False,
        "noise_schedule": settings.noise_schedule if settings.is_v4_model else "native",
    }


def _v4_common_parameters(
    settings: EngineSettings,
    *,
    prompt: str,
    negative_prompt: str,
    cfg_rescale: float | None,
) -> dict[str, Any]:
    """构造 V4 系列模型公共参数块。"""

    effective_rescale = cfg_rescale if cfg_rescale is not None else settings.cfg_rescale
    return {
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
        "skip_cfg_above_sigma": VARIETY_PLUS_SIGMA if settings.variety_plus else None,
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
        "reference_image_multiple": [],
        "reference_information_extracted_multiple": [],
        "reference_strength_multiple": [],
    }


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
    negative_prompt = merge_negative_prompts(
        settings.negative_prompt,
        spec.negative_prompt,
    )
    seed = random.randint(0, SEED_MAX)
    parameters = _base_parameters(
        settings,
        width=spec.width,
        height=spec.height,
        scale=spec.scale,
        seed=seed,
    )

    if settings.is_v4_model:
        parameters.update(
            _v4_common_parameters(
                settings,
                prompt=spec.prompt,
                negative_prompt=negative_prompt,
                cfg_rescale=spec.cfg_rescale,
            )
        )
        parameters["add_original_image"] = False
        _apply_characters(
            parameters,
            spec.characters,
            use_coords=settings.always_use_coords,
        )
        _apply_director_refs(parameters, spec.director_refs)
        if not spec.is_img2img and not spec.director_refs:
            _apply_vibes(parameters, vibes)
    else:
        parameters["negative_prompt"] = negative_prompt

    payload: dict[str, Any] = {
        "input": spec.prompt,
        "model": settings.model,
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
    )
    seed = random.randint(0, SEED_MAX)
    parameters = _base_parameters(
        settings,
        width=spec.width,
        height=spec.height,
        scale=spec.scale,
        seed=seed,
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

    if settings.is_v4_model:
        parameters.update(
            _v4_common_parameters(
                settings,
                prompt=spec.prompt,
                negative_prompt=negative_prompt,
                cfg_rescale=spec.cfg_rescale,
            )
        )
        parameters["add_original_image"] = True
    else:
        parameters["negative_prompt"] = negative_prompt

    model = settings.model
    if not model.endswith("-inpainting"):
        model = f"{model}-inpainting"

    return {
        "input": spec.prompt,
        "model": model,
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
) -> dict[str, Any]:
    """构造 Gateway 渠道 4x 放大请求体。

    Args:
        image_b64: 源图 base64
        width: 源图宽度
        height: 源图高度

    Returns:
        Gateway upscale 请求体
    """
    return {
        "image": image_b64,
        "width": width,
        "height": height,
        "response_format": "b64_json",
    }


def build_encode_vibe(
    settings: EngineSettings,
    image_b64: str,
    information_extracted: float,
) -> dict[str, Any]:
    """构造 Vibe 编码请求体（两个渠道通用）。

    Args:
        settings: 引擎配置快照
        image_b64: 原始图片 base64
        information_extracted: 信息提取量

    Returns:
        encode-vibe 请求体
    """
    return {
        "image": image_b64,
        "information_extracted": information_extracted,
        "model": settings.model,
    }


def build_gateway_generation(
    settings: EngineSettings,
    spec: GenerationSpec,
) -> dict[str, Any]:
    """构造 Gateway 渠道文生图请求体。

    Args:
        settings: 引擎配置快照
        spec: 生图请求描述

    Returns:
        Gateway generations 请求体
    """
    payload: dict[str, Any] = {
        "model": settings.model,
        "prompt": spec.prompt,
        "negative_prompt": merge_negative_prompts(
            settings.negative_prompt,
            spec.negative_prompt,
        ),
        "size": f"{spec.width}x{spec.height}",
        "n": 1,
        "steps": settings.steps,
        "scale": spec.scale if spec.scale is not None else settings.scale,
        "cfg_rescale": (
            spec.cfg_rescale if spec.cfg_rescale is not None else settings.cfg_rescale
        ),
        "sampler": settings.sampler,
        "noise_schedule": settings.noise_schedule,
        "ucPreset": settings.uc_preset,
        "quality": True,
        "variety_boost": settings.variety_plus,
        "use_coords": bool(spec.characters and settings.always_use_coords),
        "response_format": "b64_json",
    }

    if spec.characters:
        payload["characters"] = [
            {
                "prompt": character.prompt,
                "negative_prompt": character.negative_prompt,
                "position": [character.x, character.y],
                "enabled": True,
            }
            for character in spec.characters
        ]

    if spec.director_refs:
        payload["character_references"] = [
            {
                "image": ref.data,
                "type": ref.ref_type,
                "strength": ref.strength,
                "fidelity": ref.fidelity,
                "information_extracted": 1.0,
            }
            for ref in spec.director_refs
        ]

    return payload


def build_gateway_img2img(
    settings: EngineSettings,
    spec: GenerationSpec,
) -> dict[str, Any]:
    """构造 Gateway 渠道图生图请求体。

    Args:
        settings: 引擎配置快照
        spec: 生图请求描述，必须携带 source_image

    Returns:
        Gateway img2img 请求体
    """
    strength = (
        spec.strength
        if spec.strength is not None
        else settings.img2img_default_strength
    )
    return {
        "model": settings.model,
        "prompt": spec.prompt,
        "image": spec.source_image,
        "strength": strength,
        # 边缘融合：与 official 渠道行为一致，把原图叠回生成结果。
        "add_original_image": True,
        "size": f"{spec.width}x{spec.height}",
        "scale": spec.scale if spec.scale is not None else settings.scale,
        "cfg_rescale": (
            spec.cfg_rescale if spec.cfg_rescale is not None else settings.cfg_rescale
        ),
        "sampler": settings.sampler,
        "noise_schedule": settings.noise_schedule,
        "negative_prompt": merge_negative_prompts(
            settings.negative_prompt,
            spec.negative_prompt,
        ),
        "response_format": "b64_json",
    }


def build_gateway_vibe_transfer(
    settings: EngineSettings,
    spec: GenerationSpec,
    vibes: tuple[VibeAsset, ...],
) -> dict[str, Any]:
    """构造 Gateway 渠道 Vibe Transfer 请求体。

    Args:
        settings: 引擎配置快照
        spec: 生图请求描述
        vibes: 已编码的 Vibe 列表

    Returns:
        Gateway vibe-transfer 请求体
    """
    return {
        "model": settings.model,
        "prompt": spec.prompt,
        "reference_image_multiple": [vibe.data for vibe in vibes],
        "reference_strength_multiple": [vibe.strength for vibe in vibes],
        "reference_information_extracted_multiple": [
            vibe.information_extracted for vibe in vibes
        ],
        "width": spec.width,
        "height": spec.height,
        "scale": spec.scale if spec.scale is not None else settings.scale,
        "cfg_rescale": (
            spec.cfg_rescale if spec.cfg_rescale is not None else settings.cfg_rescale
        ),
        "response_format": "b64_json",
    }


def build_gateway_inpaint(
    settings: EngineSettings,
    spec: InpaintSpec,
) -> dict[str, Any]:
    """构造 Gateway 渠道局部重绘请求体。

    Args:
        settings: 引擎配置快照
        spec: 局部重绘请求描述

    Returns:
        Gateway inpainting 请求体
    """
    return {
        "model": settings.model,
        "prompt": spec.prompt,
        "image": spec.source_image,
        "mask": spec.mask,
        "strength": spec.strength,
        # 边缘融合：把原图叠回生成结果，避免重绘区域边缘生硬。
        "add_original_image": True,
        "size": f"{spec.width}x{spec.height}",
        "scale": spec.scale if spec.scale is not None else settings.scale,
        "cfg_rescale": (
            spec.cfg_rescale if spec.cfg_rescale is not None else settings.cfg_rescale
        ),
        "sampler": settings.sampler,
        "noise_schedule": settings.noise_schedule,
        "negative_prompt": merge_negative_prompts(
            settings.negative_prompt,
            spec.negative_prompt,
        ),
        "response_format": "b64_json",
    }


def build_gateway_director(spec: DirectorToolSpec) -> dict[str, Any]:
    """构造 Gateway 渠道导演工具请求体。

    Args:
        spec: 导演工具请求描述

    Returns:
        Gateway director 请求体
    """
    payload: dict[str, Any] = {
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
