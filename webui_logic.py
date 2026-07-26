"""WebUI 后端编排逻辑。

提供出图预览和配置编辑两种能力。
出图预览调用 ImageGeneratorService 走生图队列。
配置编辑支持读写 config.toml 中常用字段。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .config import ImageGeneratorConfig
from .config_persistence import save_config_atomically

if TYPE_CHECKING:
    from .services.image_service import ImageGeneratorService

DEFAULT_PLUGIN_CONFIG_PATH = Path("config/plugins/image_generator_plugin-neo/config.toml")


def load_plugin_config(config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH) -> ImageGeneratorConfig:
    """加载当前插件配置。"""
    return ImageGeneratorConfig.load(Path(config_path), auto_update=True)


# ═══════════════════════════════════════════════════════════════════════
#  配置编辑
# ═══════════════════════════════════════════════════════════════════════


def config_to_editor_payload(config: ImageGeneratorConfig, config_path: str | Path) -> dict[str, Any]:
    """将配置实例转换为前端编辑字段，不暴露任何 NovelAI 密钥。"""
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
            "always": [{"file": v.file, "enabled": v.enabled, "description": v.description, "ie": v.ie, "strength": v.strength} for v in config.vibe.always],
            "selectable": [{"file": v.file, "enabled": v.enabled, "description": v.description, "ie": v.ie, "strength": v.strength} for v in config.vibe.selectable],
        },
        "directorReference": {
            "enabled": config.director_reference.enabled,
            "selectableEnabled": config.director_reference.selectable_enabled,
            "selectable": [{"file": r.file, "enabled": r.enabled, "name": r.name, "description": r.description, "type": r.type, "fidelity": r.fidelity, "strength": r.strength} for r in config.director_reference.selectable],
        },
        "prompt": {
            "customInstructions": config.prompt.custom_instructions,
            "presets": [{"name": p.name, "trigger": p.trigger, "content": p.content} for p in config.prompt.presets],
        },
        "webui": {
            "enabled": config.webui.enabled,
            "routePath": config.webui.route_path,
        },
    }


def apply_config_overrides(config: ImageGeneratorConfig, overrides: dict[str, Any]) -> ImageGeneratorConfig:
    """将白名单字段覆盖到配置实例。"""
    if not overrides:
        return config

    # generation
    gen = overrides.get("generation", {})
    if "model" in gen:
        config.generation.model = gen["model"]
    if "noiseSchedule" in gen:
        config.generation.noise_schedule = gen["noiseSchedule"]
    if "resolution" in gen:
        config.generation.resolution = gen["resolution"]
    if "steps" in gen:
        config.generation.steps = int(gen["steps"])
    if "scale" in gen:
        config.generation.scale = float(gen["scale"])
    if "sampler" in gen:
        config.generation.sampler = gen["sampler"]
    if "promptGuidanceRescale" in gen:
        config.generation.prompt_guidance_rescale = float(gen["promptGuidanceRescale"])
    if "ucPreset" in gen:
        config.generation.uc_preset = int(gen["ucPreset"])
    if "varietyPlus" in gen:
        config.generation.variety_plus = bool(gen["varietyPlus"])
    if "styleReference" in gen:
        config.generation.style_reference = gen["styleReference"]
    if "negativePrompt" in gen:
        config.generation.negative_prompt = gen["negativePrompt"]
    if "characterPrompt" in gen:
        config.generation.character_prompt = gen["characterPrompt"]
    if "alwaysUseCoords" in gen:
        config.generation.always_use_coords = bool(gen["alwaysUseCoords"])
    if "allowSkipStyle" in gen:
        config.generation.allow_skip_style = bool(gen["allowSkipStyle"])

    # prompt
    pmt = overrides.get("prompt", {})
    if "customInstructions" in pmt:
        config.prompt.custom_instructions = pmt["customInstructions"]
    if "presets" in pmt:
        from .config import PromptPresetConfig
        config.prompt.presets = [
            PromptPresetConfig(
                name=str(p.get("name", "")),
                trigger=str(p.get("trigger", "")),
                content=str(p.get("content", "")),
            )
            for p in pmt["presets"]
            if p.get("name")
        ]

    # vibe
    vb = overrides.get("vibe", {})
    if "alwaysEnabled" in vb:
        config.vibe.always_enabled = bool(vb["alwaysEnabled"])
    if "selectableEnabled" in vb:
        config.vibe.selectable_enabled = bool(vb["selectableEnabled"])
    if "always" in vb:
        from .config import VibeItemConfig
        config.vibe.always = [
            VibeItemConfig(
                file=str(v.get("file", "")),
                enabled=bool(v.get("enabled", True)),
                description=str(v.get("description", "")),
                ie=float(v.get("ie", 1.0)),
                strength=float(v.get("strength", 0.6)),
            )
            for v in vb["always"]
            if v.get("file")
        ]
    if "selectable" in vb:
        from .config import VibeItemConfig
        config.vibe.selectable = [
            VibeItemConfig(
                file=str(v.get("file", "")),
                enabled=bool(v.get("enabled", True)),
                description=str(v.get("description", "")),
                ie=float(v.get("ie", 1.0)),
                strength=float(v.get("strength", 0.6)),
            )
            for v in vb["selectable"]
            if v.get("file")
        ]

    # director_reference
    dr = overrides.get("directorReference", {})
    if "enabled" in dr:
        config.director_reference.enabled = bool(dr["enabled"])
    if "selectableEnabled" in dr:
        config.director_reference.selectable_enabled = bool(dr["selectableEnabled"])
    if "selectable" in dr:
        from .config import DirectorReferenceItemConfig
        config.director_reference.selectable = [
            DirectorReferenceItemConfig(
                file=str(r.get("file", "")),
                enabled=bool(r.get("enabled", True)),
                name=str(r.get("name", "")),
                description=str(r.get("description", "")),
                type=cast(
                    Literal["character", "style", "character&style"],
                    str(r.get("type", "character&style")),
                ),
                fidelity=float(r.get("fidelity", 1.0)),
                strength=float(r.get("strength", 1.0)),
            )
            for r in dr["selectable"]
            if r.get("file")
        ]

    # webui
    ui = overrides.get("webui", {})
    if "enabled" in ui:
        config.webui.enabled = bool(ui["enabled"])
    if "routePath" in ui:
        config.webui.route_path = ui["routePath"]

    return ImageGeneratorConfig.model_validate(config.model_dump(mode="python"))


def save_plugin_config(
    overrides: dict[str, Any],
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> ImageGeneratorConfig:
    """按白名单字段完整校验并原子保存配置。"""

    path = Path(config_path)
    config = apply_config_overrides(load_plugin_config(path), overrides)
    save_config_atomically(path, config)
    return config


# ═══════════════════════════════════════════════════════════════════════
#  出图预览
# ═══════════════════════════════════════════════════════════════════════


async def generate_preview_image(
    *,
    service: "ImageGeneratorService",
    prompt: str,
    negative_prompt: str = "",
    resolution: str = "832x1216",
    scale: float | None = None,
    cfg_rescale: float | None = None,
    selected_vibes: list[str] | None = None,
) -> dict[str, Any]:
    """调用 ImageGeneratorService 走生图队列出一张预览图。

    Args:
        prompt: 正面提示词
        negative_prompt: 额外负面提示词
        resolution: 画幅 "832x1216" / "1216x832" / "1024x1024"
        scale: 临时覆盖引导比例（None 则用配置值）
        cfg_rescale: 临时覆盖 cfg_rescale（None 则用配置值）
        selected_vibes: 选择的 Vibe 名称列表

    Returns:
        包含 imageDataUrl / prompt / 实际使用的参数值 的字典
    """
    config = cast(ImageGeneratorConfig, service.plugin.config)

    # 实际使用的参数值（留空时回退到配置值）
    actual_scale = scale if scale is not None else config.generation.scale
    actual_cfg_rescale = (
        cfg_rescale
        if cfg_rescale is not None
        else config.generation.prompt_guidance_rescale
    )

    # 解析分辨率
    try:
        w_str, h_str = resolution.lower().replace("×", "x").split("x")
        width, height = int(w_str.strip()), int(h_str.strip())
    except Exception:
        width, height = 832, 1216

    success, message, image_path = await service.generate_image(
        prompt=prompt,
        user_id="webui_preview",
        negative_prompt=negative_prompt or None,
        width=width,
        height=height,
        is_img2img=False,
        from_command=False,
        selected_vibe_names=selected_vibes,
        scale=scale,
        cfg_rescale=cfg_rescale,
    )

    if not success or not image_path:
        return {
            "imageDataUrl": None,
            "prompt": prompt,
            "error": message,
            "actualScale": actual_scale,
            "actualCfgRescale": actual_cfg_rescale,
            "actualResolution": f"{width}x{height}",
        }

    # 读取图片为 base64
    try:
        image_file = Path(image_path)
        if image_file.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("预览图片超过 32 MB 限制")
        img_b64 = base64.b64encode(image_file.read_bytes()).decode()
        return {
            "imageDataUrl": f"data:image/png;base64,{img_b64}",
            "prompt": prompt,
            "imagePath": image_path,
            "actualScale": actual_scale,
            "actualCfgRescale": actual_cfg_rescale,
            "actualResolution": f"{width}x{height}",
        }
    except Exception as e:
        return {
            "imageDataUrl": None,
            "prompt": prompt,
            "error": str(e),
            "actualScale": actual_scale,
            "actualCfgRescale": actual_cfg_rescale,
            "actualResolution": f"{width}x{height}",
        }
