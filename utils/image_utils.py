"""图片处理工具模块。

提供图片文件读取、编码、验证等通用功能，无框架依赖。
"""

import base64
import io
import os
from pathlib import Path
from typing import Optional

from PIL import Image

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("image_generator_plugin.utils")


class ImageUtils:
    """图片处理工具类。"""

    @staticmethod
    def read_image_as_base64(
        image_path: str,
        *,
        strip_metadata: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """读取图片文件并转换为 base64 编码。

        Args:
            image_path: 图片文件路径
            strip_metadata: 是否剥离 PNG 元数据（种子、提示词等生图信息）。
                开启后仅影响编码结果，不修改磁盘上的原文件。

        Returns:
            (是否成功, 错误消息或成功提示, base64 编码或 None)
        """
        try:
            if not os.path.exists(image_path):
                logger.error(f"图片文件不存在: {image_path}")
                return False, f"图片文件不存在: {image_path}", None

            file_size = os.path.getsize(image_path)
            if file_size == 0:
                logger.error(f"图片文件大小为 0: {image_path}")
                return False, "图片文件为空", None

            logger.info(f"读取图片: {image_path}, 大小: {file_size} 字节 ({file_size / 1024:.2f} KB)")

            if strip_metadata:
                img_data = ImageUtils.strip_png_metadata(image_path)
            else:
                with open(image_path, "rb") as f:
                    img_data = f.read()

            if len(img_data) == 0:
                logger.error("读取的图片数据为空")
                return False, "读取的图片数据为空", None

            img_base64 = base64.b64encode(img_data).decode("utf-8")
            logger.info(f"成功读取图片，base64 长度: {len(img_base64)} 字符")

            return True, "图片读取成功", img_base64

        except Exception as e:
            logger.error(f"读取或编码图片失败: {e}", exc_info=True)
            return False, f"读取图片失败: {e}", None

    @staticmethod
    def strip_png_metadata(image_path: str) -> bytes:
        """剥离 PNG 元数据并返回干净的字节数据。

        使用 PIL 重新保存图片到内存，去除所有 PNG 辅助块
        （包括 NovelAI 嵌入的 Comment、Software、Generation Time 等）。
        图片像素数据保持不变，视觉无差别。

        Args:
            image_path: 图片文件路径

        Returns:
            剥离元数据后的 PNG 字节数据
        """
        with Image.open(image_path) as img:
            # 将图片强制转换为 RGB 模式，以完全丢弃可能包含元数据隐写的 Alpha 通道
            if img.mode != "RGB":
                img = img.convert("RGB")
            # 清空 PIL 读取到的文本元数据，防止 save() 重新写入 tEXt/iTXt chunk
            img.info.clear()
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=False)
            return buf.getvalue()

    @staticmethod
    def validate_image_file(image_path: str) -> tuple[bool, str]:
        """验证图片文件是否有效。

        Args:
            image_path: 图片文件路径

        Returns:
            (是否有效, 错误消息或空字符串)
        """
        if not os.path.exists(image_path):
            return False, f"文件不存在: {image_path}"

        file_size = os.path.getsize(image_path)
        if file_size == 0:
            return False, "文件大小为 0"

        valid_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        ext = Path(image_path).suffix.lower()
        if ext not in valid_extensions:
            return False, f"不支持的图片格式: {ext}"

        return True, ""

    @staticmethod
    def cleanup_temp_file(file_path: str, *, keep_file: bool = True) -> None:
        """清理临时文件。

        Args:
            file_path: 文件路径
            keep_file: 是否保留文件（默认 True，用于调试）
        """
        if not keep_file:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"已删除临时文件: {file_path}")
            except Exception as e:
                logger.warning(f"删除临时文件失败: {e}")
        else:
            logger.info(f"临时文件已保留: {file_path}")
