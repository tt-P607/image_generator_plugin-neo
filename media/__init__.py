"""媒体处理包。

image_ops 提供纯图片运算，message_images 负责从聊天消息/媒体库中取图。
"""

import hashlib
from typing import Literal

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_media

from . import image_ops
from .message_images import (
    extract_image_by_media_id,
    extract_image_from_stream,
    extract_image_from_stream_id,
)

logger = get_logger("image_generator_plugin.media")

pending_generated_images: dict[str, list[str]] = {}


async def send_generated_image(
    image_b64: str,
    stream_id: str,
    mode: Literal["vlm", "base64"],
    reply_to: str | None = None,
    *,
    inject_into_context: bool = False,
) -> bool:
    """发送生成图片；VLM 识别异常时按普通占位符发送。"""
    context_mode = (
        {"vlm": "description", "base64": "native"}[mode]
        if inject_into_context else "placeholder"
    )
    try:
        sent = await send_media(
            "image", image_b64,
            stream_id=stream_id,
            reply_to=reply_to,
            context_mode=context_mode,
        )
        if sent and context_mode == "native":
            media_id = hashlib.sha256(image_b64.encode("utf-8")).hexdigest()
            pending = pending_generated_images.setdefault(stream_id, [])
            if media_id not in pending:
                pending.append(media_id)
        return sent
    except Exception as error:
        if context_mode != "description":
            raise
        logger.warning(f"生成图片 VLM 识别失败（{type(error).__name__}），改用图片占位符")
        return await send_media(
            "image", image_b64,
            stream_id=stream_id,
            reply_to=reply_to,
            context_mode="placeholder",
        )


__all__ = [
    "extract_image_by_media_id",
    "extract_image_from_stream",
    "extract_image_from_stream_id",
    "image_ops",
    "send_generated_image",
]
