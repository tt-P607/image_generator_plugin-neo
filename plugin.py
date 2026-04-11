"""NovelAI 图片生成插件。

支持文生图、图生图、Vibe 参考图等功能。
使用任务队列串行化所有生图请求，防止 429 封号。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin

from .actions.draw_action import DrawAction
from .actions.selfie_action import GenerateSelfieAction
from .commands.image_command import (
    ImageEditCommand,
    ImageGeneratorCommand,
    VibeManagementCommand,
)
from .config import ImageGeneratorConfig
from .services.image_service import ImageGeneratorService

logger = get_logger("image_generator_plugin")


@register_plugin
class ImageGeneratorPlugin(BasePlugin):
    """NovelAI 图片生成插件。

    支持文生图、图生图、Vibe 参考图等功能。
    """

    plugin_name: str = "image_generator_plugin"
    plugin_description: str = "基于 NovelAI 官方 API 的 AI 图片生成插件"
    plugin_version: str = "2.0.0"

    configs = [ImageGeneratorConfig]

    def __init__(self, config: ImageGeneratorConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置实例
        """
        super().__init__(config)
        self.image_service: ImageGeneratorService | None = None

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后的回调，初始化服务并注入自定义场景说明。"""
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

        # 将 character_prompt 注入 generate_selfie 描述，使 LLM 明确知道角色特征
        character_prompt = cfg.generation.character_prompt.strip()
        if character_prompt:
            GenerateSelfieAction.action_description = (
                GenerateSelfieAction.action_description.rstrip()
                + f"\n\n【你的角色特征标签（已内置，scene_description/pose_or_action 中无需重复）】\n{character_prompt}"
            )
            logger.debug("已将 character_prompt 注入 generate_selfie 描述")

        # 将自定义场景说明追加到两个 action 的描述，使 Chatter 侧感知使用时机
        custom = cfg.prompt.custom_instructions.strip()
        if custom:
            for action_cls in (DrawAction, GenerateSelfieAction):
                action_cls.action_description = (
                    action_cls.action_description.rstrip() + "\n\n" + custom
                )
            logger.debug("已将自定义场景说明追加到 draw_image / generate_selfie 描述")

        # 将可选 Vibe 列表注入 draw_image 描述，使 LLM 知道可选哪些画风
        if cfg.vibe.selectable_enabled and cfg.vibe.selectable:
            from pathlib import Path as _Path
            vibe_names = [_Path(item.file).stem for item in cfg.vibe.selectable]
            vibe_block = (
                "\n\n【可选 Vibe 画风列表（通过 selected_vibes 参数选择，可多选，逗号分隔）】\n"
                + "\n".join(f"  - {name}" for name in vibe_names)
            )
            DrawAction.action_description = DrawAction.action_description.rstrip() + vibe_block
            logger.debug(f"已将 {len(cfg.vibe.selectable)} 个可选 Vibe 注入 draw_image 描述")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前的回调，清理资源。"""
        if self.image_service:
            await self.image_service.cleanup()
            logger.info("ImageGeneratorService 已清理")

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        根据配置决定是否启用 Action 和 Command 组件。
        """
        cfg = self.config
        if not isinstance(cfg, ImageGeneratorConfig) or not cfg.plugin.enabled:
            return []

        components: list[type] = []

        # Action 组件
        if cfg.components.action_enabled:
            components.append(DrawAction)
            components.append(GenerateSelfieAction)

        # Command 组件
        if cfg.components.command_enabled:
            components.append(ImageGeneratorCommand)
            components.append(ImageEditCommand)
            components.append(VibeManagementCommand)

        # Service 组件
        components.append(ImageGeneratorService)

        return components
