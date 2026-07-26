"""图片命令基类。

统一处理中文别名匹配、命令正文提取、用户作用域计算，
以及"后台出图 + 发送结果"的公共流程。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, cast

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseCommand
from src.app.plugin_system.types import PermissionLevel

from .. import background
from ..engine import ImageEngine, ImageResult
from ..engine import storage
from . import replies

if TYPE_CHECKING:
    from ..plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.command")


class BaseImageCommand(BaseCommand):
    """图片命令基类。

    子类需声明 ``name``、``description`` 与 ``command_aliases``。
    """

    command_aliases: list[str] = []
    permission_level: PermissionLevel = PermissionLevel.OPERATOR

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配命令名或中文别名。

        Args:
            parts: 命令分割后的片段

        Returns:
            匹配到的片段数，未匹配时为 0
        """
        matched = super().match(parts)
        if matched:
            return matched
        if parts and parts[0] in cls.command_aliases:
            return 1
        return 0

    @property
    def image_plugin(self) -> "ImageGeneratorPlugin":
        """所属插件实例。"""

        from ..plugin import ImageGeneratorPlugin

        return cast(ImageGeneratorPlugin, self.plugin)

    @property
    def engine(self) -> ImageEngine | None:
        """当前图片生成引擎，未就绪时返回 None。"""

        engine = self.image_plugin.engine
        if engine is None:
            logger.error("图片生成引擎尚未初始化")
        return engine

    @property
    def user_scope(self) -> str:
        """命令状态使用的用户/流隔离键。"""

        message = self._message
        if message is not None and message.sender_id:
            return f"{message.platform}:{message.sender_id}"
        return f"stream:{self.stream_id}"

    def command_body(self, subcommand: str = "") -> str:
        """提取命令名之后的原始正文。

        框架传入的子路由文本会被 shlex 拆分，含空格与特殊符号的提示词
        无法还原，因此这里直接从原始消息中截取。

        Args:
            subcommand: 需要一并剥离的子命令名，如 "draw"

        Returns:
            命令正文，未匹配到时返回空串
        """
        message = self._message
        if message is None:
            return ""

        source = message.processed_plain_text
        if not source and isinstance(message.content, str):
            source = message.content
        if not source:
            return ""

        text = source.strip()
        body = ""
        for name in (self.name, *self.command_aliases):
            prefix = f"{self.command_prefix}{name}"
            if text == prefix:
                return ""
            if text.startswith(f"{prefix} "):
                body = text[len(prefix) :].strip()
                break

        if not body or not subcommand:
            return body

        lowered = body.lower()
        if lowered == subcommand:
            return ""
        if lowered.startswith(f"{subcommand} "):
            return body[len(subcommand) :].strip()
        return body

    async def reply(self, message: str) -> None:
        """向当前聊天流发送文本。

        Args:
            message: 文本内容
        """
        await send_text(message, stream_id=self.stream_id)

    async def run_generation(
        self,
        work: Callable[[], Awaitable[ImageResult]],
        *,
        task_name: str,
        purpose: str,
        success_hints: list[str],
        success_key: str,
    ) -> tuple[bool, str]:
        """在后台出图并发送结果，命令本身立即返回。

        Args:
            work: 产出图片的异步任务
            task_name: 后台任务名
            purpose: 任务用途标识
            success_hints: 成功后的提示语候选
            success_key: 提示语轮换标识

        Returns:
            (是否已提交, 面向调用方的说明)
        """
        stream_id = self.stream_id
        reply_to = self.message_id or None
        strip_metadata = self.image_plugin.image_config.generation.strip_metadata_command

        async def _execute() -> None:
            try:
                result = await work()
            except Exception as error:  # noqa: BLE001 - 后台任务需兜底避免静默丢失
                logger.error(f"后台出图任务异常: {error}", exc_info=True)
                await send_text(
                    replies.pick(replies.GENERATE_ERROR_HINTS, "gen_error").format(
                        error=replies.humanize_error(str(error))
                    ),
                    stream_id=stream_id,
                )
                return

            if not result.success or result.path is None:
                await send_text(
                    replies.pick(replies.GENERATE_ERROR_HINTS, "gen_error").format(
                        error=replies.humanize_error(result.message)
                    ),
                    stream_id=stream_id,
                )
                return

            try:
                image_b64 = storage.read_image_base64(
                    Path(result.path),
                    strip_metadata=strip_metadata,
                )
            except (OSError, ValueError) as error:
                await send_text(
                    replies.pick(replies.ERROR_HINTS, "error").format(
                        error=replies.humanize_error(str(error))
                    ),
                    stream_id=stream_id,
                )
                return

            await send_image(image_b64, stream_id=stream_id, reply_to=reply_to)
            await send_text(
                replies.pick(success_hints, success_key),
                stream_id=stream_id,
            )

        task = background.spawn(
            self.image_plugin,
            _execute(),
            name=task_name,
            purpose=purpose,
            stream_id=stream_id,
        )
        if task is None:
            await self.reply("任务提交失败了，稍后再试试吧")
            return False, "后台任务创建失败"

        return True, "图片生成任务已提交"
