"""聊天消息中的图片来源解析。

Action 与 Command 共用本模块，统一通过媒体 ID（media_id，即图片数据的
SHA256 哈希）经 Media API 精确取图，避免旧方式逐条消息读 base64 造成的错位。

- Action 场景：LLM 上下文中直接携带 ``[图片(media_id)]`` 占位符，
  Action 把占位符括号内的哈希值填入 ``media_id`` 参数，由
  :func:`extract_image_by_media_id` 精确读取。
- Command 场景：命令依赖用户"引用图片再发命令"，仍需定位最近图片消息，
  但只提取其中的 ``image_id`` 再走媒体库，不再读取消息内 base64。
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


async def extract_image_by_media_id(media_id: str) -> str | None:
    """通过媒体 ID（图片 SHA256 哈希）精确读取图片 base64。

    从上下文 ``[图片(media_id)]`` 占位符中提取的哈希值即 media_id，
    经 Media API 回查媒体库拿到文件路径后读取编码。

    Args:
        media_id: 媒体 ID，即上下文占位符括号内的 SHA256 哈希

    Returns:
        图片 base64，未找到或读取失败时返回 None
    """
    info = await get_media_info(media_id)
    if info is None:
        logger.warning(f"media_id={media_id} 未在媒体库中找到")
        return None

    file_path = info.get("path")
    if not isinstance(file_path, str) or not file_path:
        logger.warning(f"media_id={media_id} 无有效文件路径")
        return None

    path = Path(file_path)
    if not path.is_file():
        logger.warning(f"media_id={media_id} 对应文件不存在: {path}")
        return None

    return base64.b64encode(path.read_bytes()).decode("utf-8")


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


async def _extract_image_id_from_stream(
    stream: "ChatStream",
    message: "Message | None" = None,
) -> str | None:
    """从指定聊天流的最近消息中提取图片 media_id。

    Args:
        stream: 目标聊天流
        message: 触发消息，可为 None

    Returns:
        图片 media_id（SHA256 哈希），未找到时返回 None
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
            image_id = media.get("image_id")
            if isinstance(image_id, str) and image_id.strip():
                return image_id
    return None


async def extract_image_from_stream(
    stream: "ChatStream",
    message: "Message | None" = None,
) -> str | None:
    """从指定聊天流的最近消息中提取图片 base64。

    只提取最近图片消息的 media_id，再经媒体库精确读取，不读取消息内 base64。

    Args:
        stream: 目标聊天流
        message: 触发消息，可为 None

    Returns:
        图片 base64，未找到时返回 None
    """
    image_id = await _extract_image_id_from_stream(stream, message)
    if not image_id:
        return None
    return await extract_image_by_media_id(image_id)


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
