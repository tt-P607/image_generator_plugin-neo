"""WebUI Router 组件。

把出图预览与配置编辑界面挂载到主程序 HTTP 服务。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, BaseRouter

from .app import create_app
from .logic import DEFAULT_CONFIG_PATH

if TYPE_CHECKING:
    from ..plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.router")

FALLBACK_ROUTE_PATH = "/plugins/image-generator"


class WebUIRouter(BaseRouter):
    """出图测试与配置编辑 WebUI。"""

    name: str = "image_generator_webui"
    description: str = "出图测试与配置编辑 WebUI"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化 Router 并确定挂载路径。

        Args:
            plugin: 所属插件实例
        """
        image_plugin = cast("ImageGeneratorPlugin", plugin)
        route_path = image_plugin.image_config.webui.route_path.strip()
        self.custom_route_path = route_path or FALLBACK_ROUTE_PATH
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载 WebUI 子应用。"""

        sub_app = create_app(
            plugin=cast("ImageGeneratorPlugin", self.plugin),
            config_path=DEFAULT_CONFIG_PATH,
        )
        self.app.mount("/", sub_app)

    async def startup(self) -> None:
        """记录 WebUI 挂载完成状态。"""

        logger.info(f"WebUI 已挂载到主程序 HTTP 路径: {self.get_route_path()}")
