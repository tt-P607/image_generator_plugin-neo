"""聊天消息中的图片来源解析。

Action 与 Command 共用本模块，通过公开 Stream/Media API 查找最近图片，
避免直接访问 StreamManager 内部缓存。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.media_api import get_media_info
from src.app.plugin_system.api.stream_api import get_stream

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream

logger = get_logger("image_generator_plugin.message_images")


def _message_candidates(
    stream: "ChatStream",
    message: "Message | None",
) -> list[Any]:
    """按从近到远顺序构建候选消息列表。

    Args:
        stream: 目标聊天流
        message: 触发消息，可为 None

    Returns:
        去重后的候选消息列表
    """
    candidates: list[Any] = []
    if message is not None:
        candidates.append(message)

    context = stream.context
    if context.current_message is not None and context.current_message is not message:
        candidates.append(context.current_message)
    candidates.extend(reversed(context.unread_messages))
    candidates.extend(reversed(context.history_messages))

    unique: list[Any] = []
    seen: set[int] = set()
    for candidate in candidates:
        identity = id(candidate)
        if identity not in seen:
            seen.add(identity)
            unique.append(candidate)
    return unique


async def _load_media_data(media: dict[str, Any]) -> str | None:
    """优先从媒体缓存读取图片，缺失时回退到消息内 base64。

    Args:
        media: 消息中的媒体条目

    Returns:
        图片 base64，读取失败时返回 None
    """
    image_id = media.get("image_id")
    if isinstance(image_id, str) and image_id.strip():
        info = await get_media_info(image_id)
        if info is not None:
            file_path = info.get("path")
            if isinstance(file_path, str) and file_path:
                path = Path(file_path)
                if path.is_file():
                    return base64.b64encode(path.read_bytes()).decode("utf-8")

    data = media.get("data")
    return data if isinstance(data, str) and data else None


async def extract_image_from_stream(
    stream: "ChatStream",
    message: "Message | None" = None,
) -> str | None:
    """从指定聊天流的最近消息中提取图片 base64。

    Args:
        stream: 目标聊天流
        message: 触发消息，可为 None

    Returns:
        图片 base64，未找到时返回 None
    """
    for candidate in _message_candidates(stream, message):
        content = candidate.content
        if not isinstance(content, dict):
            continue
        media_items = content.get("media", [])
        if not isinstance(media_items, list):
            continue

        for media in media_items:
            if not isinstance(media, dict) or media.get("type") != "image":
                continue
            try:
                data = await _load_media_data(media)
            except OSError as error:
                logger.warning(f"读取消息图片失败: {error}")
                continue
            if data:
                return data
    return None


async def extract_image_from_stream_id(
    stream_id: str,
    message: "Message | None" = None,
) -> str | None:
    """通过公开 Stream API 获取聊天流并提取最近图片。

    Args:
        stream_id: 聊天流 ID
        message: 触发消息，可为 None

    Returns:
        图片 base64，未找到时返回 None
    """
    stream = await get_stream(stream_id)
    if stream is None:
        return None
    return await extract_image_from_stream(stream, message)
