"""媒体处理包。

image_ops 提供纯图片运算，message_images 负责从聊天流中取图。
"""

from . import image_ops
from .message_images import extract_image_from_stream, extract_image_from_stream_id

__all__ = [
    "extract_image_from_stream",
    "extract_image_from_stream_id",
    "image_ops",
]
