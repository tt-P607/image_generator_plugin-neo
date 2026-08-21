"""NovelAI 图片生成插件。

支持文生图、图生图、局部重绘、Vibe 参考与导演工具，
兼容 NovelAI 官方 API 与 OpenAI 协议的 Gateway 中转服务。
"""

from __future__ import annotations

from typing import cast

from src.app.plugin_system.api.action_api import clear_schema_cache
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.core.prompt import SystemReminderBucket, SystemReminderInsertType
from src.kernel.concurrency import get_task_manager

from .actions import (
    BgRemovalAction,
    ColorizeAction,
    DeclutterAction,
    DrawAction,
    EditImageAction,
    EmotionAction,
    InpaintAction,
    LineartAction,
    SketchAction,
)
from .commands import (
    ImageEditCommand,
    ImageGeneratorCommand,
    ImageReferenceCommand,
    VibeManagementCommand,
)
from .config import ImageGeneratorConfig
from .descriptions import build_draw_description
from .engine import ImageEngine
from .services.image_service import ImageGeneratorService
from .webui import WebUIRouter

logger = get_logger("image_generator_plugin")

PLUGIN_NAME = "image_generator_plugin-neo"
DRAW_ACTION_SIGNATURE = f"{PLUGIN_NAME}:action:draw_image"
_RULE_REMINDER_NAME = "image_generator_rule"

DIRECTOR_ACTIONS: tuple[tuple[str, type], ...] = (
    ("director_declutter_enabled", DeclutterAction),
    ("director_bg_removal_enabled", BgRemovalAction),
    ("director_lineart_enabled", LineartAction),
    ("director_sketch_enabled", SketchAction),
    ("director_colorize_enabled", ColorizeAction),
    ("director_emotion_enabled", EmotionAction),
)


@register_plugin
class ImageGeneratorPlugin(BasePlugin):
    """NovelAI 图片生成插件。"""

    plugin_name: str = PLUGIN_NAME
    plugin_version: str = "2.4.0"

    configs: list[type] = [ImageGeneratorConfig]
    dependent_components: list[str] = []

    def __init__(self, config: ImageGeneratorConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置实例
        """
        super().__init__(config)
        self.engine: ImageEngine | None = None
        self._background_task_ids: set[str] = set()

    @property
    def image_config(self) -> ImageGeneratorConfig:
        """当前插件配置。

        Returns:
            配置实例；框架未注入时返回一份默认配置
        """
        config = self.config
        if isinstance(config, ImageGeneratorConfig):
            return config
        return ImageGeneratorConfig()

    def register_background_task(self, task_id: str) -> None:
        """登记插件拥有的后台任务。

        Args:
            task_id: 任务 ID
        """
        self._background_task_ids.add(task_id)

    def discard_background_task(self, task_id: str) -> None:
        """移除已完成的后台任务登记。

        Args:
            task_id: 任务 ID
        """
        self._background_task_ids.discard(task_id)

    async def on_plugin_loaded(self) -> None:
        """启动引擎并注入画图 Action 描述。"""

        config = self.image_config
        if not config.plugin.enabled:
            logger.info("ImageGeneratorPlugin 已在配置中禁用")
            return

        engine = ImageEngine(config)
        await engine.start()
        self.engine = engine
        self._refresh_draw_description(config)
        self._sync_rule_reminder(config)
        logger.info("ImageGeneratorPlugin 初始化完成")

    async def on_plugin_unloaded(self) -> None:
        """取消后台任务、清理 system reminder 并释放引擎资源。"""

        task_manager = get_task_manager()
        for task_id in tuple(self._background_task_ids):
            task_manager.cancel_task(task_id)
        self._background_task_ids.clear()

        self._clear_rule_reminder()

        if self.engine is not None:
            await self.engine.close()
            self.engine = None

    async def apply_config(self, config: ImageGeneratorConfig) -> None:
        """应用新配置并刷新引擎与 Action 描述。

        Args:
            config: 已校验的新配置实例
        """
        self.config = cast(ImageGeneratorConfig, config)
        if self.engine is not None:
            await self.engine.reload(config)
        self._refresh_draw_description(config)
        self._sync_rule_reminder(config)

    def _sync_rule_reminder(self, config: ImageGeneratorConfig) -> None:
        """按配置同步生图规则到 actor system reminder。

        开关开启时把完整生图规则以 DYNAMIC 方式注入 actor bucket，
        贴近当前轮次输入；关闭时清理注入。

        Args:
            config: 当前配置实例
        """
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        if not config.prompt.inject_rule_reminder:
            store.delete(
                SystemReminderBucket.ACTOR.value,
                _RULE_REMINDER_NAME,
            )
            return

        content = (
            "【图片生成规则 — 调用 draw_image 时必须严格遵守以下规范】\n"
            "以下是 NovelAI 生图的核心规则，涵盖标签语法、角色、画幅、"
            "参考图与内置画风/负面词。每次调用 draw_image 前请按此规则组织参数。\n\n"
            f"{build_draw_description(config)}"
        )
        store.set(
            SystemReminderBucket.ACTOR.value,
            name=_RULE_REMINDER_NAME,
            content=content,
            insert_type=SystemReminderInsertType.DYNAMIC,
        )
        logger.info("已同步生图规则到 actor system reminder")

    def _clear_rule_reminder(self) -> None:
        """清理插件注入的 actor system reminder。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete(SystemReminderBucket.ACTOR.value, _RULE_REMINDER_NAME)

    def _refresh_draw_description(self, config: ImageGeneratorConfig) -> None:
        """按配置重建画图 Action 的描述并让 schema 缓存失效。

        Args:
            config: 当前配置实例
        """
        DrawAction.description = build_draw_description(config)
        clear_schema_cache(DRAW_ACTION_SIGNATURE)
        logger.debug("画图 Action 描述已刷新")

    def get_components(self) -> list[type]:
        """按配置开关返回启用的组件类。

        Returns:
            组件类列表
        """
        config = self.image_config
        if not config.plugin.enabled:
            return []

        components: list[type] = [ImageGeneratorService]

        if config.components.action_enabled:
            components.append(DrawAction)
            if config.components.inpaint_action_enabled:
                components.append(InpaintAction)
            if config.components.edit_action_enabled:
                components.append(EditImageAction)
            components.extend(
                action
                for flag, action in DIRECTOR_ACTIONS
                if getattr(config.components, flag)
            )

        if config.components.command_enabled:
            components.extend(
                [
                    ImageGeneratorCommand,
                    ImageEditCommand,
                    ImageReferenceCommand,
                    VibeManagementCommand,
                ]
            )

        if config.webui.enabled:
            components.append(WebUIRouter)

        return components
