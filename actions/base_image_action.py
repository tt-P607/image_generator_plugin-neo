"""图片 Action 基类。

为图片生成相关的 Action 提供通用功能：
- 获取服务实例
- 生成并发送图片的统一封装
- 画幅解析
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image
from src.core.components.base.action import BaseAction

from ..utils.image_utils import ImageUtils

if TYPE_CHECKING:
    from ..services.image_service import ImageGeneratorService

logger = get_logger("image_generator_plugin.base_image_action")


class BaseImageAction(BaseAction):
    """图片 Action 基类。

    封装图片生成和发送的通用逻辑。
    """

    def get_service(self) -> Optional["ImageGeneratorService"]:
        """获取图片生成服务实例。

        Returns:
            服务实例或 None
        """
        service = getattr(self.plugin, "image_service", None)
        if not service:
            logger.error("无法获取图片生成服务")
        return service

    async def generate_and_send_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        success_message: str = "[内部：已发送图片]",
        error_prefix: str = "生成失败",
    ) -> tuple[bool, str]:
        """生成图片并发送（统一封装方法）。

        Args:
            prompt: 图片生成提示词
            negative_prompt: 负面提示词（可选）
            width: 图片宽度
            height: 图片高度
            success_message: 成功时返回的消息
            error_prefix: 错误消息前缀

        Returns:
            (是否成功, 消息)
        """
        service = self.get_service()
        if not service:
            return False, "图片生成服务不可用"

        # 从 chat_stream 获取用户信息
        user_id = self.chat_stream.context.triggering_user_id or ""
        group_id = self.chat_stream.extra.get("group_id") if hasattr(self.chat_stream, "extra") else None

        logger.info(f"生成图片 - 提示词: {prompt}")
        try:
            success, msg, image_path = await service.generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                user_id=str(user_id),
                group_id=str(group_id) if group_id else None,
            )

            if success and image_path:
                return await self.read_and_send_image(
                    image_path,
                    success_message=success_message,
                    keep_file=True,
                )
            logger.error(f"图片生成失败: {msg}")
            return False, f"{error_prefix}: {msg}"

        except Exception as e:
            logger.error(f"生成图片异常: {e}", exc_info=True)
            return False, f"{error_prefix}: {e}"

    async def read_and_send_image(
        self,
        image_path: str,
        success_message: str = "[内部：已发送图片]",
        keep_file: bool = True,
    ) -> tuple[bool, str]:
        """读取图片文件并发送。

        Args:
            image_path: 图片文件路径
            success_message: 成功时返回的消息
            keep_file: 是否保留临时文件

        Returns:
            (是否成功, 消息)
        """
        success, msg, img_base64 = ImageUtils.read_image_as_base64(image_path)

        if not success or not img_base64:
            return False, msg

        try:
            await send_image(img_base64, stream_id=self.chat_stream.stream_id)
            logger.info("图片已发送")
            ImageUtils.cleanup_temp_file(image_path, keep_file=True)
            return True, success_message
        except Exception as e:
            logger.error(f"发送图片失败: {e}", exc_info=True)
            return False, f"发送图片失败: {e}"

    def _parse_resolution(self, resolution: str, default: str = "1024x1024") -> tuple[int, int]:
        """解析画幅字符串为宽高，失败时自动回退到默认值。

        Args:
            resolution: 画幅字符串，如 '1216x832'
            default: 默认画幅

        Returns:
            (width, height)
        """
        try:
            width_str, height_str = resolution.lower().split("x")
            width = int(width_str.strip())
            height = int(height_str.strip())

            valid_sizes = [(1216, 832), (832, 1216), (1024, 1024)]
            if (width, height) not in valid_sizes:
                logger.warning(f"不支持的画幅 {width}x{height}，使用默认 {default}")
                return self._parse_resolution(default, default="1024x1024")

            return width, height
        except Exception as e:
            logger.error(f"解析画幅失败: {resolution}, 错误: {e}, 使用默认 {default}")
            if default != "1024x1024":
                return self._parse_resolution(default, default="1024x1024")
            return 1024, 1024
