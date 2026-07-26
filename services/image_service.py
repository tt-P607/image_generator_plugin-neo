"""图片生成服务组件。

对其他插件暴露稳定的生图能力。服务本身不持有状态——
框架每次 ``get_service()`` 都会新建实例，真实状态由插件持有的引擎维护。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, BaseService

from ..engine import (
    DirectorToolSpec,
    GenerationSpec,
    ImageEngine,
    ImageResult,
    InpaintSpec,
)

if TYPE_CHECKING:
    from ..plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.service")

ENGINE_UNAVAILABLE = "图片生成引擎尚未初始化"


class ImageGeneratorService(BaseService):
    """图片生成服务。

    提供文生图、图生图、局部重绘与导演工具四类能力，
    所有请求都会进入引擎的串行队列，避免并发触发平台限流。
    """

    name: str = "image_generator"
    description: str = "NovelAI 图片生成服务"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化服务。

        Args:
            plugin: 所属插件实例
        """
        super().__init__(plugin)

    @property
    def engine(self) -> ImageEngine | None:
        """插件持有的图片生成引擎，未就绪时返回 None。"""

        plugin = cast("ImageGeneratorPlugin", self.plugin)
        engine = plugin.engine
        if engine is None:
            logger.error(ENGINE_UNAVAILABLE)
        return engine

    async def generate_image(self, spec: GenerationSpec) -> ImageResult:
        """执行文生图或图生图。

        Args:
            spec: 生图请求描述

        Returns:
            执行结果
        """
        engine = self.engine
        if engine is None:
            return ImageResult.failure(ENGINE_UNAVAILABLE)
        return await engine.generate(spec)

    async def inpaint_image(self, spec: InpaintSpec) -> ImageResult:
        """执行局部重绘。

        Args:
            spec: 局部重绘请求描述

        Returns:
            执行结果
        """
        engine = self.engine
        if engine is None:
            return ImageResult.failure(ENGINE_UNAVAILABLE)
        return await engine.inpaint(spec)

    async def run_director_tool(self, spec: DirectorToolSpec) -> ImageResult:
        """执行导演工具处理。

        Args:
            spec: 导演工具请求描述

        Returns:
            执行结果
        """
        engine = self.engine
        if engine is None:
            return ImageResult.failure(ENGINE_UNAVAILABLE)
        return await engine.run_director_tool(spec)

    def list_selectable_vibes(self) -> tuple[str, ...]:
        """列出当前可供选择的 Vibe 名称。

        Returns:
            Vibe 名称元组
        """
        engine = self.engine
        if engine is None:
            return ()
        return engine.assets.selectable_vibe_names

    def list_director_references(self) -> tuple[str, ...]:
        """列出当前可用的精密参考名称。

        Returns:
            参考名称元组
        """
        engine = self.engine
        if engine is None:
            return ()
        return engine.assets.director_ref_names
