"""WebUI 后端编排逻辑。

负责配置的读取、白名单覆盖、保存，以及调用引擎出预览图。
API Key 等敏感字段不会出现在任何返回值中。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Literal, cast

from ..config import (
    DirectorReferenceItemConfig,
    ImageGeneratorConfig,
    PromptPresetConfig,
    VibeItemConfig,
)
from ..engine import GenerationSpec, ImageEngine
from .persistence import save_config_atomically

DEFAULT_CONFIG_PATH = Path("config/plugins/image_generator_plugin-neo/config.toml")
PREVIEW_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_PREVIEW_RESOLUTION = (832, 1216)


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> ImageGeneratorConfig:
    """从磁盘加载插件配置。

    Args:
        config_path: 配置文件路径

    Returns:
        配置实例
    """
    return ImageGeneratorConfig.load(Path(config_path), auto_update=True)


def config_to_payload(
    config: ImageGeneratorConfig,
    config_path: str | Path,
) -> dict[str, Any]:
    """把配置转换为前端可编辑的结构，不含任何密钥。

    Args:
        config: 配置实例
        config_path: 配置文件路径，用于前端展示

    Returns:
        前端编辑载荷
    """
    return {
        "configPath": str(Path(config_path)),
        "plugin": {"enabled": config.plugin.enabled},
        "api": {
            "channel": config.api.channel,
            "apiKeyCount": len(config.api.api_keys),
            "baseUrl": config.api.base_url,
            "proxy": config.api.proxy,
            "cooldown": config.api.cooldown,
        },
        "generation": {
            "model": config.generation.model,
            "availableModels": config.generation.available_models,
            "modelAliases": config.generation.model_aliases,
            "noiseSchedule": config.generation.noise_schedule,
            "resolution": config.generation.resolution,
            "steps": config.generation.steps,
            "scale": config.generation.scale,
            "sampler": config.generation.sampler,
            "promptGuidanceRescale": config.generation.prompt_guidance_rescale,
            "ucPreset": config.generation.uc_preset,
            "varietyPlus": config.generation.variety_plus,
            "styleReference": config.generation.style_reference,
            "negativePrompt": config.generation.negative_prompt,
            "characterPrompt": config.generation.character_prompt,
            "alwaysUseCoords": config.generation.always_use_coords,
            "allowSkipStyle": config.generation.allow_skip_style,
        },
        "vibe": {
            "alwaysEnabled": config.vibe.always_enabled,
            "selectableEnabled": config.vibe.selectable_enabled,
            "always": [_vibe_to_payload(item) for item in config.vibe.always],
            "selectable": [_vibe_to_payload(item) for item in config.vibe.selectable],
        },
        "directorReference": {
            "enabled": config.director_reference.enabled,
            "selectableEnabled": config.director_reference.selectable_enabled,
            "selectable": [
                _reference_to_payload(item)
                for item in config.director_reference.selectable
            ],
        },
        "prompt": {
            "customInstructions": config.prompt.custom_instructions,
            "presets": [
                {
                    "name": preset.name,
                    "trigger": preset.trigger,
                    "content": preset.content,
                }
                for preset in config.prompt.presets
            ],
        },
        "webui": {
            "enabled": config.webui.enabled,
            "routePath": config.webui.route_path,
        },
    }


def _vibe_to_payload(item: VibeItemConfig) -> dict[str, Any]:
    """把 Vibe 配置项转成前端结构。"""

    return {
        "file": item.file,
        "enabled": item.enabled,
        "description": item.description,
        "ie": item.ie,
        "strength": item.strength,
    }


def _reference_to_payload(item: DirectorReferenceItemConfig) -> dict[str, Any]:
    """把精密参考配置项转成前端结构。"""

    return {
        "file": item.file,
        "enabled": item.enabled,
        "name": item.name,
        "description": item.description,
        "type": item.type,
        "fidelity": item.fidelity,
        "strength": item.strength,
    }


_GENERATION_FIELDS: dict[str, tuple[str, type]] = {
    "model": ("model", str),
    "noiseSchedule": ("noise_schedule", str),
    "resolution": ("resolution", str),
    "steps": ("steps", int),
    "scale": ("scale", float),
    "sampler": ("sampler", str),
    "promptGuidanceRescale": ("prompt_guidance_rescale", float),
    "ucPreset": ("uc_preset", int),
    "varietyPlus": ("variety_plus", bool),
    "styleReference": ("style_reference", str),
    "negativePrompt": ("negative_prompt", str),
    "characterPrompt": ("character_prompt", str),
    "alwaysUseCoords": ("always_use_coords", bool),
    "allowSkipStyle": ("allow_skip_style", bool),
}


def apply_overrides(
    config: ImageGeneratorConfig,
    overrides: dict[str, Any],
) -> ImageGeneratorConfig:
    """把白名单字段覆盖到配置并重新做完整校验。

    Args:
        config: 基线配置实例
        overrides: 前端提交的覆盖字段

    Returns:
        校验通过的新配置实例
    """
    if not overrides:
        return config

    generation = overrides.get("generation", {})
    for key, (attribute, caster) in _GENERATION_FIELDS.items():
        if key in generation:
            setattr(config.generation, attribute, caster(generation[key]))
    if "availableModels" in generation:
        config.generation.available_models = [
            str(model).strip()
            for model in generation["availableModels"]
            if str(model).strip()
        ]
    if "modelAliases" in generation:
        raw_aliases = generation["modelAliases"]
        config.generation.model_aliases = (
            {
                str(name).strip(): str(target).strip()
                for name, target in raw_aliases.items()
                if str(name).strip() and str(target).strip()
            }
            if isinstance(raw_aliases, dict)
            else {}
        )

    prompt = overrides.get("prompt", {})
    if "customInstructions" in prompt:
        config.prompt.custom_instructions = str(prompt["customInstructions"])
    if "presets" in prompt:
        config.prompt.presets = [
            PromptPresetConfig(
                name=str(item.get("name", "")),
                trigger=str(item.get("trigger", "")),
                content=str(item.get("content", "")),
            )
            for item in prompt["presets"]
            if item.get("name")
        ]

    vibe = overrides.get("vibe", {})
    if "alwaysEnabled" in vibe:
        config.vibe.always_enabled = bool(vibe["alwaysEnabled"])
    if "selectableEnabled" in vibe:
        config.vibe.selectable_enabled = bool(vibe["selectableEnabled"])
    if "always" in vibe:
        config.vibe.always = _parse_vibe_items(vibe["always"])
    if "selectable" in vibe:
        config.vibe.selectable = _parse_vibe_items(vibe["selectable"])

    reference = overrides.get("directorReference", {})
    if "enabled" in reference:
        config.director_reference.enabled = bool(reference["enabled"])
    if "selectableEnabled" in reference:
        config.director_reference.selectable_enabled = bool(
            reference["selectableEnabled"]
        )
    if "selectable" in reference:
        config.director_reference.selectable = _parse_reference_items(
            reference["selectable"]
        )

    webui = overrides.get("webui", {})
    if "enabled" in webui:
        config.webui.enabled = bool(webui["enabled"])
    if "routePath" in webui:
        config.webui.route_path = str(webui["routePath"])

    return ImageGeneratorConfig.model_validate(config.model_dump(mode="python"))


def _parse_vibe_items(items: list[dict[str, Any]]) -> list[VibeItemConfig]:
    """把前端提交的 Vibe 列表转成配置项。"""

    return [
        VibeItemConfig(
            file=str(item.get("file", "")),
            enabled=bool(item.get("enabled", True)),
            description=str(item.get("description", "")),
            ie=float(item.get("ie", 1.0)),
            strength=float(item.get("strength", 0.6)),
        )
        for item in items
        if item.get("file")
    ]


def _parse_reference_items(
    items: list[dict[str, Any]],
) -> list[DirectorReferenceItemConfig]:
    """把前端提交的精密参考列表转成配置项。"""

    return [
        DirectorReferenceItemConfig(
            file=str(item.get("file", "")),
            enabled=bool(item.get("enabled", True)),
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
            type=cast(
                Literal["character", "style", "character&style"],
                str(item.get("type", "character&style")),
            ),
            fidelity=float(item.get("fidelity", 1.0)),
            strength=float(item.get("strength", 1.0)),
        )
        for item in items
        if item.get("file")
    ]


def save_config(
    overrides: dict[str, Any],
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> ImageGeneratorConfig:
    """校验覆盖字段并原子保存配置。

    Args:
        overrides: 前端提交的覆盖字段
        config_path: 配置文件路径

    Returns:
        保存后的配置实例
    """
    path = Path(config_path)
    config = apply_overrides(load_config(path), overrides)
    save_config_atomically(path, config)
    return config


def parse_resolution(resolution: str) -> tuple[int, int]:
    """解析预览画幅文本，失败时回退到默认竖图。

    Args:
        resolution: 画幅文本，如 "832x1216"

    Returns:
        (宽, 高)
    """
    parts = resolution.lower().replace("×", "x").split("x")
    if len(parts) != 2:
        return DEFAULT_PREVIEW_RESOLUTION
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return DEFAULT_PREVIEW_RESOLUTION


async def generate_preview(
    *,
    engine: ImageEngine,
    prompt: str,
    negative_prompt: str = "",
    resolution: str = "832x1216",
    model: str = "",
    steps: int | None = None,
    scale: float | None = None,
    cfg_rescale: float | None = None,
    variety_plus: bool | None = None,
    render_text: bool = False,
    selected_vibes: list[str] | None = None,
) -> dict[str, Any]:
    """走生图队列出一张预览图。

    Args:
        engine: 图片生成引擎
        prompt: 正面提示词
        negative_prompt: 额外负面提示词
        resolution: 画幅文本
        model: 本次使用的生图模型，留空使用默认模型
        steps: 临时覆盖采样步数
        scale: 临时覆盖引导比例
        cfg_rescale: 临时覆盖 cfg_rescale
        variety_plus: 临时覆盖 Variety+
        render_text: 是否需要生成画面文字
        selected_vibes: 选择的 Vibe 名称

    Returns:
        含预览图 data URL 与实际生效参数的字典
    """
    settings = engine.settings
    width, height = parse_resolution(resolution)
    actual_scale = scale if scale is not None else settings.scale
    actual_cfg_rescale = cfg_rescale if cfg_rescale is not None else settings.cfg_rescale
    actual_model = model.strip() or settings.model
    actual_steps = steps if steps is not None else settings.steps

    result = await engine.generate(
        GenerationSpec(
            prompt=prompt,
            user_id="webui_preview",
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            model=model.strip() or None,
            steps=steps,
            scale=scale,
            cfg_rescale=cfg_rescale,
            variety_plus=variety_plus,
            render_text=render_text,
            selected_vibe_names=tuple(selected_vibes or ()),
        )
    )

    payload: dict[str, Any] = {
        "imageDataUrl": None,
        "prompt": prompt,
        "actualModel": actual_model,
        "actualSteps": actual_steps,
        "actualScale": actual_scale,
        "actualCfgRescale": actual_cfg_rescale,
        "actualResolution": f"{width}x{height}",
    }

    if not result.success or result.path is None:
        payload["error"] = result.message
        return payload

    image_file = Path(result.path)
    if image_file.stat().st_size > PREVIEW_MAX_BYTES:
        payload["error"] = "预览图片超过 32 MB 限制"
        return payload

    encoded = base64.b64encode(image_file.read_bytes()).decode()
    payload["imageDataUrl"] = f"data:image/png;base64,{encoded}"
    return payload
