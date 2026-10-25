"""图片 Action 基类。

统一处理引擎取用、图片来源解析、后台执行与结果发送，
子类只需描述"要做什么"。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.media_api import get_media_info
from src.app.plugin_system.api.send_api import format_send_result, send_text_result
from src.app.plugin_system.base import BaseAction, PlatformSendStatus

from .. import background
from ..config import ImageGeneratorConfig
from ..engine import ImageEngine, ImageResult
from ..engine import storage
from ..media import (
    extract_image_by_media_id,
    extract_image_from_stream,
    send_generated_image_result,
)

if TYPE_CHECKING:
    from ..plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.action")

VALID_RESOLUTIONS = {(1216, 832), (832, 1216), (1024, 1024)}
FALLBACK_RESOLUTION = (1024, 1024)
class BaseImageAction(BaseAction):
    """图片 Action 基类。

    提供引擎访问、图片来源解析和"后台执行 + 发送结果"的统一流程。
    """

    @property
    def image_plugin(self) -> "ImageGeneratorPlugin":
        """所属插件实例。"""

        from ..plugin import ImageGeneratorPlugin

        return cast(ImageGeneratorPlugin, self.plugin)

    @property
    def engine(self) -> ImageEngine | None:
        """当前图片生成引擎，未就绪时返回 None。"""

        from ..plugin import ImageGeneratorPlugin

        plugin = cast(ImageGeneratorPlugin, self.plugin)
        engine: ImageEngine | None = plugin.engine  # type: ignore[union-attr]
        if engine is None:
            logger.error("图片生成引擎尚未初始化")
        return engine

    @property
    def plugin_config(self) -> ImageGeneratorConfig:
        """当前插件配置。"""

        from ..plugin import ImageGeneratorPlugin

        plugin = cast(ImageGeneratorPlugin, self.plugin)
        return plugin.image_config  # type: ignore[union-attr]

    async def resolve_source_image(
        self,
        media_id: str = "",
    ) -> str | None:
        """按媒体 ID 解析待处理图片。

        media_id（上下文 ``[图片(media_id)]`` 占位符中的哈希）优先，
        未提供时回退到最近消息中的图片。

        Args:
            media_id: 上下文占位符中的媒体 ID（SHA256 哈希），可为空

        Returns:
            图片 base64，未找到时返回 None
        """
        if media_id.strip():
            loaded = await extract_image_by_media_id(media_id.strip())
            if loaded is not None:
                return loaded
            logger.warning(f"media_id={media_id} 取图失败")
            return None

        return await extract_image_from_stream(self.chat_stream)

    def parse_resolution(self, resolution: str, default: str) -> tuple[int, int]:
        """解析画幅字符串，非法值按配置与默认值依次回退。

        Args:
            resolution: 模型给出的画幅，如 "832x1216"
            default: 该 Action 的偏好画幅

        Returns:
            (宽, 高)
        """
        engine = self.engine
        configured = engine.settings.resolution if engine is not None else ""

        for candidate in (resolution, configured, default):
            parsed = _parse_size(candidate)
            if parsed is not None:
                if candidate is not resolution:
                    logger.warning(f"画幅 {resolution!r} 无效，改用 {candidate}")
                return parsed
        return FALLBACK_RESOLUTION

    async def run_in_background(
        self,
        work: Callable[[], Awaitable[ImageResult | tuple[ImageResult, ...]]],
        *,
        task_name: str,
        purpose: str,
        success_message: str,
        error_prefix: str,
    ) -> tuple[bool, str]:
        """在后台执行图片任务，成功后发送图片并返回媒体 ID。

        等待被取消时任务仍会跑完，并把失败信息补偿发送给用户。

        Args:
            work: 产出图片的异步任务
            task_name: 后台任务名
            purpose: 任务用途标识
            success_message: 成功时返回给模型的消息
            error_prefix: 失败消息前缀

        Returns:
            (是否成功, 面向模型的消息)
        """
        detached = {"value": False}

        async def _execute() -> tuple[bool, str]:
            result = await work()
            results = result if isinstance(result, tuple) else (result,)
            media_ids: list[str] = []
            postprocess_warnings: list[str] = []
            for image_result in results:
                if not image_result.success or image_result.path is None:
                    logger.error(f"{error_prefix}: {image_result.message}")
                    message = f"{error_prefix}: {image_result.message}"
                    if detached["value"]:
                        await self.notify(message)
                    return False, message

                path = Path(image_result.path)
                sent, media_id_or_error = await self._send_image(path)
                if not sent:
                    if detached["value"]:
                        logger.warning(f"图片发送状态: {media_id_or_error}")
                        await send_text_result(
                            "图片未能确认发送，请检查平台状态后再试。",
                            stream_id=self.chat_stream.stream_id,
                        )
                    return False, media_id_or_error
                if len(media_id_or_error) == 64 and all(
                    character in "0123456789abcdef" for character in media_id_or_error
                ):
                    media_ids.append(media_id_or_error)
                else:
                    postprocess_warnings.append(media_id_or_error)

            message = (
                f"{success_message}（media_id: {', '.join(media_ids)}）"
                if media_ids else success_message
            )
            if postprocess_warnings:
                message = f"{message}；本地后处理提示：" + "；".join(postprocess_warnings)
            return True, message

        try:
            return await background.run_shielded(
                self.image_plugin,
                _execute,
                name=task_name,
                purpose=purpose,
                stream_id=self.chat_stream.stream_id,
                on_detach=lambda: detached.__setitem__("value", True),
            )
        except RuntimeError as error:
            logger.error(f"{error_prefix}: {error}")
            return False, f"{error_prefix}: {error}"

    async def _send_image(self, path: Path) -> tuple[bool, str]:
        """读取图片并发送到当前聊天流。

        Args:
            path: 图片路径

        Returns:
            (是否成功, 媒体 ID 或失败说明)
        """
        strip_metadata = self.plugin_config.generation.strip_metadata_action
        try:
            image_b64 = storage.read_image_base64(path, strip_metadata=strip_metadata)
        except (OSError, ValueError) as error:
            logger.error(f"读取图片失败: {error}", exc_info=True)
            return False, f"读取图片失败: {error}"

        send_result = await send_generated_image_result(
            image_b64,
            stream_id=self.chat_stream.stream_id,
            mode=self.plugin_config.plugin.output_image_context,
            inject_into_context=self.plugin_config.plugin.should_inject_generated_image(
                self.chat_stream.chat_type,
            ),
        )
        sent, receipt = format_send_result(send_result, "图片已发送")
        if not sent:
            return False, receipt
        if send_result.status is not PlatformSendStatus.SENT:
            return False, receipt
        media_id = hashlib.sha256(image_b64.encode("utf-8")).hexdigest()
        try:
            info = await get_media_info(media_id)
        except Exception as error:
            return True, f"{receipt}；但读取媒体缓存时失败：{error}"
        if info is None or info.get("image_id") != media_id or not info.get("path"):
            return True, f"{receipt}，但媒体缓存不可回查，无法通过 media_id 引用"
        logger.info(f"图片已发送: {path.name}")
        if receipt != "图片已发送":
            return True, f"{media_id}；{receipt}"
        return True, media_id

    async def notify(self, message: str) -> None:
        """向当前聊天流发送一条提示文本。

        Args:
            message: 提示内容
        """
        result = await send_text_result(message, stream_id=self.chat_stream.stream_id)
        sent, receipt = format_send_result(result, "通知已发送")
        if not sent or receipt != "通知已发送":
            logger.warning(f"图片任务通知发送状态: {receipt}")

    @property
    def triggering_user_id(self) -> str:
        """触发本次动作的用户标识。"""

        context: Any = self.chat_stream.context
        return str(context.triggering_user_id or "")


def _parse_size(value: str) -> tuple[int, int] | None:
    """解析 "宽x高" 文本，非法或不受支持时返回 None。

    Args:
        value: 画幅文本

    Returns:
        (宽, 高) 或 None
    """
    if not value:
        return None
    parts = value.lower().split("x")
    if len(parts) != 2:
        return None
    try:
        size = (int(parts[0].strip()), int(parts[1].strip()))
    except ValueError:
        return None
    return size if size in VALID_RESOLUTIONS else None
