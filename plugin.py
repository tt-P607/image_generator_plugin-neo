"""NovelAI 图片生成插件。

支持文生图、图生图、Vibe 参考图等功能。
使用任务队列串行化所有生图请求，防止 429 封号。
"""

from __future__ import annotations

from src.app.plugin_system.api.action_api import clear_schema_cache
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager

from .actions.base_image_action import BaseImageAction
from .actions.director_tool_action import (
    BgRemovalAction,
    ColorizeAction,
    DeclutterAction,
    EmotionAction,
    LineartAction,
    SketchAction,
)
from .actions.draw_action import DrawAction
from .actions.inpaint_action import InpaintAction
from .commands.image_command import (
    ImageEditCommand,
    ImageGeneratorCommand,
    ImageRefCommand,
    VibeManagementCommand,
)
from .config import ImageGeneratorConfig
from .router import WebUIRouter
from .services.image_service import ImageGeneratorService

logger = get_logger("image_generator_plugin")


@register_plugin
class ImageGeneratorPlugin(BasePlugin):
    """NovelAI 图片生成插件。

    支持文生图、图生图、Vibe 参考图等功能。
    """

    plugin_name: str = "image_generator_plugin-neo"
    plugin_description: str = "基于 NovelAI 官方 API 的 AI 图片生成插件"

    configs = [ImageGeneratorConfig]

    def __init__(self, config: ImageGeneratorConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置实例
        """
        super().__init__(config)
        self.image_service: ImageGeneratorService | None = None
        self._background_task_ids: set[str] = set()

    def register_background_task(self, task_id: str) -> None:
        """登记插件拥有的后台任务。"""

        self._background_task_ids.add(task_id)

    def discard_background_task(self, task_id: str) -> None:
        """移除已完成的后台任务登记。"""

        self._background_task_ids.discard(task_id)

    async def refresh_runtime_config(self, config: ImageGeneratorConfig) -> None:
        """应用已校验配置并刷新共享 Service。"""

        self.config = config
        if self.image_service is not None:
            await self.image_service.refresh_config()
        self._inject_action_description(config)

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后的回调，初始化服务并注入动态信息。"""
        cfg = self.config
        if not isinstance(cfg, ImageGeneratorConfig) or not cfg.plugin.enabled:
            logger.info("ImageGeneratorPlugin 已在配置中禁用")
            return

        try:
            logger.info("初始化 ImageGeneratorPlugin...")
            self.image_service = ImageGeneratorService(self)
            await self.image_service.initialize()

            logger.info("ImageGeneratorService 已初始化")
        except Exception as e:
            logger.error(f"ImageGeneratorPlugin 初始化失败: {e}", exc_info=True)
            raise

        # === 注入动态信息到 Action 的 description ===
        self._inject_action_description(cfg)

    def _inject_action_description(self, cfg: ImageGeneratorConfig) -> None:
        """从稳定基础文本幂等重建 Action description。

        Args:
            cfg: 插件配置实例
        """
        DrawAction.description = DrawAction.base_action_description
        BaseImageAction._preset_negative_prompt = ""

        # 1. 画风标签预设（默认拼接，LLM 可通过 no_style 跳过——可由 allow_skip_style 关闭）
        style_ref = cfg.generation.style_reference.strip()
        if style_ref:
            allow_skip = cfg.generation.allow_skip_style
            style_block = (
                "【默认画风标签（系统自动拼接到提示词最前面）】\n"
                f"  {style_ref}\n"
            )
            if allow_skip:
                style_block += (
                    "  ⚠️ 如果当前场景不适合此画风（如特殊形态、表情包、纯风景等），"
                    "请在 content_description 中加入 `no_style` 来跳过画风注入。"
                )
            else:
                style_block += "  ⚠️ 画风标签为强制注入，不可跳过。"
            DrawAction.description = (
                DrawAction.description.rstrip() + "\n\n" + style_block
            )
            logger.debug("已将画风标签注入 Action description")

        # 2. 预设负面提示词
        preset_negative = cfg.generation.negative_prompt.strip()
        if preset_negative:
            BaseImageAction._preset_negative_prompt = preset_negative
            DrawAction.description = (
                DrawAction.description.rstrip()
                + f"\n\n【已内置负面提示词（无需重复填写）】\n{preset_negative}"
            )
            logger.debug("已将预设负面提示词注入 Action description")

        # 3. 角色外观描述
        character_prompt = cfg.generation.character_prompt.strip()
        if character_prompt:
            DrawAction.description = (
                DrawAction.description.rstrip()
                + f"\n\n【角色外观描述（画自己时参考）】\n{character_prompt}"
            )
            logger.debug("已将角色外观描述注入 Action description")

        # 4. 结构化预设列表（跳过 content 为空的纯参考图预设，它们只是内部触发机制）
        visible_presets = [p for p in cfg.prompt.presets if p.content.strip()]
        if visible_presets:
            preset_lines = ["【预设场景指令】"]
            for preset in visible_presets:
                trigger_hint = f"（{preset.trigger}）" if preset.trigger else ""
                preset_lines.append(
                    f"  - {preset.name}{trigger_hint}：{preset.content}"
                )
            DrawAction.description = (
                DrawAction.description.rstrip()
                + "\n\n" + "\n".join(preset_lines)
            )
            logger.debug(f"已将 {len(visible_presets)} 条预设注入 Action description（跳过 {len(cfg.prompt.presets) - len(visible_presets)} 条空内容预设）")

        # 5. 自定义提示词指引（自由文本）
        custom = cfg.prompt.custom_instructions.strip()
        if custom:
            DrawAction.description = (
                DrawAction.description.rstrip()
                + f"\n\n【自定义指引】\n{custom}"
            )
            logger.debug("已将自定义提示词指引注入 Action description")

        # 6. 可选 Vibe 列表（带场景描述）
        if cfg.vibe.selectable_enabled and cfg.vibe.selectable:
            from pathlib import Path

            vibe_lines = ["【可选 Vibe 画风列表（通过 selected_vibes 参数选择，可多选，逗号分隔）】"]
            for item in cfg.vibe.selectable:
                vibe_name = Path(item.file).stem
                if item.description:
                    vibe_lines.append(f"  - {vibe_name}：{item.description}")
                else:
                    vibe_lines.append(f"  - {vibe_name}")
            DrawAction.description = (
                DrawAction.description.rstrip()
                + "\n\n" + "\n".join(vibe_lines)
            )
            logger.debug(f"已将 {len(cfg.vibe.selectable)} 个可选 Vibe 注入 Action description")

        # 7. 可选精密参考列表（带场景描述）
        if (
            cfg.director_reference.enabled
            and cfg.director_reference.selectable_enabled
            and cfg.director_reference.selectable
        ):
            from pathlib import Path

            ref_lines = ["【可用精密参考列表（通过 selected_director_refs 参数选择，可多选，逗号分隔）】"]
            for item in cfg.director_reference.selectable:
                if not item.enabled:
                    continue
                ref_name = item.name or Path(item.file).stem
                if item.description:
                    ref_lines.append(f"  - {ref_name}：{item.description}")
                else:
                    ref_lines.append(f"  - {ref_name}")
            DrawAction.description = (
                DrawAction.description.rstrip()
                + "\n\n" + "\n".join(ref_lines)
            )
            logger.debug(f"已将 {len(cfg.director_reference.selectable)} 个精密参考注入 Action description")

        clear_schema_cache("image_generator_plugin-neo:action:draw_image")

    async def on_plugin_unloaded(self) -> None:
        """取消插件任务并清理共享 Service。"""

        task_manager = get_task_manager()
        for task_id in tuple(self._background_task_ids):
            task_manager.cancel_task(task_id)
        self._background_task_ids.clear()

        if self.image_service is not None:
            await self.image_service.cleanup()
            self.image_service = None
            logger.info("ImageGeneratorService 已清理")

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        根据配置决定是否启用各组件。
        """
        cfg = self.config
        if not isinstance(cfg, ImageGeneratorConfig) or not cfg.plugin.enabled:
            return []

        components: list[type] = []

        # Action 组件（执行实际生图）
        if cfg.components.action_enabled:
            components.append(DrawAction)
            if cfg.components.inpaint_action_enabled:
                components.append(InpaintAction)
            # 导演工具：按各自配置开关分别注册独立 Action
            if cfg.components.director_declutter_enabled:
                components.append(DeclutterAction)
            if cfg.components.director_bg_removal_enabled:
                components.append(BgRemovalAction)
            if cfg.components.director_lineart_enabled:
                components.append(LineartAction)
            if cfg.components.director_sketch_enabled:
                components.append(SketchAction)
            if cfg.components.director_colorize_enabled:
                components.append(ColorizeAction)
            if cfg.components.director_emotion_enabled:
                components.append(EmotionAction)

        # Command 组件
        if cfg.components.command_enabled:
            components.append(ImageGeneratorCommand)
            components.append(ImageEditCommand)
            components.append(ImageRefCommand)
            components.append(VibeManagementCommand)

        # Service 组件
        components.append(ImageGeneratorService)

        # WebUI Router（WebUI 启用时注册）
        if cfg.webui.enabled:
            components.append(WebUIRouter)

        return components
