"""WebUI Router — 挂载到主程序 HTTP 服务器。

提供出图测试和配置编辑的可视化界面。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseRouter

from .config import ImageGeneratorConfig
from .webui_app import create_app
from .webui_logic import initialize_webui_runtime

logger = get_logger("image_generator_plugin.router")


class WebUIRouter(BaseRouter):
    """将 WebUI 挂载到主程序 HTTP 服务器。

    提供出图测试和配置编辑的可视化界面。
    """

    router_name = "image_generator_webui"
    router_description = "出图测试与配置编辑 WebUI"

    def __init__(self, plugin: Any) -> None:
        """初始化 WebUI Router。

        Args:
            plugin: 插件实例
        """
        config = getattr(plugin, "config", None)
        if isinstance(config, ImageGeneratorConfig):
            route_path = config.webui.route_path.strip()
            self.custom_route_path = route_path or "/plugins/image-generator"
        self._sub_app: FastAPI | None = None
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载 WebUI 子应用。"""
        config_path = Path("config/plugins/image_generator_plugin-neo/config.toml")
        self._sub_app = create_app(config_path=config_path, initialize_runtime=False)
        self.app.mount("/", self._sub_app)

    async def startup(self) -> None:
        """初始化 WebUI 所需的最小运行时。"""
        initialize_webui_runtime()
        logger.info(
            f"WebUI 已挂载到主程序 HTTP 路径: {self.get_route_path()}"
        )

    async def shutdown(self) -> None:
        """清理资源。"""
        pass
