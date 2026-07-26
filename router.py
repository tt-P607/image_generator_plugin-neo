"""WebUI Router — 挂载到主程序 HTTP 服务器。

提供出图测试和配置编辑的可视化界面。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseRouter

from .config import ImageGeneratorConfig
from .webui_app import ImageGeneratorPluginProtocol, create_app

if TYPE_CHECKING:
    from .plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.router")


class WebUIRouter(BaseRouter):
    """将 WebUI 挂载到主程序 HTTP 服务器。

    提供出图测试和配置编辑的可视化界面。
    """

    name = "image_generator_webui"
    description = "出图测试与配置编辑 WebUI"

    def __init__(self, plugin: "ImageGeneratorPlugin") -> None:
        """初始化 WebUI Router。

        Args:
            plugin: 插件实例
        """
        config = plugin.config
        if isinstance(config, ImageGeneratorConfig):
            route_path = config.webui.route_path.strip()
            self.custom_route_path = route_path or "/plugins/image-generator"
        self._sub_app: FastAPI | None = None
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载 WebUI 子应用。"""
        config_path = Path("config/plugins/image_generator_plugin-neo/config.toml")
        self._sub_app = create_app(
            plugin=cast(ImageGeneratorPluginProtocol, self.plugin),
            config_path=config_path,
        )
        self.app.mount("/", self._sub_app)

    async def startup(self) -> None:
        """记录 WebUI 挂载完成状态。"""
        logger.info(
            f"WebUI 已挂载到主程序 HTTP 路径: {self.get_route_path()}"
        )

    async def shutdown(self) -> None:
        """释放 Router 持有的子应用引用。"""

        self._sub_app = None
