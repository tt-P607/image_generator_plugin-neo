"""媒体处理包。

image_ops 提供纯图片运算，message_images 负责从聊天消息/媒体库中取图。
"""

import hashlib
from typing import Literal

from src.app.plugin_system.api.send_api import send_media_result
from src.core.components.types import PlatformSendResult, PlatformSendStatus

from . import image_ops
from .message_images import (
    extract_image_by_media_id,
    extract_image_from_stream,
    extract_image_from_stream_id,
)

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
    result = await send_generated_image_result(
        image_b64,
        stream_id,
        mode,
        reply_to,
        inject_into_context=inject_into_context,
    )
    return result.success


async def send_generated_image_result(
    image_b64: str,
    stream_id: str,
    mode: Literal["vlm", "base64"],
    reply_to: str | None = None,
    *,
    inject_into_context: bool = False,
) -> PlatformSendResult:
    """发送生成图片并保留平台状态和原因。"""
    context_mode = (
        {"vlm": "description", "base64": "native"}[mode]
        if inject_into_context else "placeholder"
    )
    result = await send_media_result(
        "image", image_b64,
        stream_id=stream_id,
        reply_to=reply_to,
        context_mode=context_mode,
    )
    if (
        result.status is PlatformSendStatus.SENT
        and result.success
        and context_mode == "native"
    ):
        media_id = hashlib.sha256(image_b64.encode("utf-8")).hexdigest()
        pending = pending_generated_images.setdefault(stream_id, [])
        if media_id not in pending:
            pending.append(media_id)
    return result


__all__ = [
    "extract_image_by_media_id",
    "extract_image_from_stream",
    "extract_image_from_stream_id",
    "image_ops",
    "send_generated_image",
    "send_generated_image_result",
]
