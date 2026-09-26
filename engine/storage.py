"""生成结果的本地落盘。

统一处理 ZIP 解包与图片字节写入。
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

from src.app.plugin_system.api.log_api import get_logger

from ..media import image_ops

logger = get_logger("image_generator_plugin.storage")


def save_image_bytes(image_bytes: bytes, target_dir: Path) -> Path:
    """将图片字节写入目标目录。

    Args:
        image_bytes: 图片字节
        target_dir: 保存目录

    Returns:
        实际写入的文件路径

    Raises:
        ValueError: 图片数据为空
    """
    if not image_bytes:
        raise ValueError("图片数据为空")

    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{uuid.uuid4()}.png"
    file_path.write_bytes(image_bytes)

    width, height = image_ops.read_image_size(
        base64.b64encode(image_bytes).decode("utf-8")
    )
    logger.info(f"图片已保存: {file_path}, 尺寸: {width}x{height}")
    return file_path


def save_response_payload(payload: bytes, target_dir: Path) -> Path:
    """保存 API 响应，自动识别 ZIP 与裸图片两种格式。

    Args:
        payload: 响应字节
        target_dir: 保存目录

    Returns:
        实际写入的文件路径

    Raises:
        ValueError: ZIP 中不含图片或数据为空
    """
    if image_ops.is_zip_payload(payload):
        extracted = image_ops.extract_first_image_from_zip(payload)
        if extracted is None:
            raise ValueError("ZIP 文件中没有找到图片")
        return save_image_bytes(extracted, target_dir)
    return save_image_bytes(payload, target_dir)


def save_base64_image(b64_data: str, target_dir: Path) -> Path:
    """保存 base64 编码的图片。

    Args:
        b64_data: 图片 base64，可带 data URL 前缀
        target_dir: 保存目录

    Returns:
        实际写入的文件路径
    """
    raw = base64.b64decode(image_ops.strip_data_url_prefix(b64_data))
    return save_image_bytes(raw, target_dir)


def read_image_base64(path: Path, *, strip_metadata: bool = False) -> str:
    """读取本地图片为 base64。

    Args:
        path: 图片路径
        strip_metadata: 是否剥离 PNG 元数据后再编码

    Returns:
        图片 base64

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件内容为空
    """
    if not path.is_file():
        raise FileNotFoundError(f"图片文件不存在: {path}")

    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"图片文件为空: {path}")

    if strip_metadata:
        raw = image_ops.strip_png_metadata(raw)
    return base64.b64encode(raw).decode("utf-8")
