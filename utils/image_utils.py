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
        """剥离 PNG 文本元数据并破坏 Alpha 通道中的隐写信息。

        重新保存时不携带 PNG 文本块。存在透明通道时保留 0/255 端点，
        将中间透明度量化为 16 级，在尽量保持视觉透明度的同时破坏
        Alpha 低位中可能携带的 NovelAI 隐写数据。

        Args:
            image_path: 图片文件路径

        Returns:
            剥离元数据后的 PNG 字节数据
        """
        with Image.open(image_path) as img:
            clean_image = img.convert("RGBA") if "A" in img.getbands() else img.convert("RGB")
            if clean_image.mode == "RGBA":
                red, green, blue, alpha = clean_image.split()
                alpha = alpha.point(
                    lambda value: value
                    if value in (0, 255)
                    else round(value / 17) * 17
                )
                clean_image = Image.merge("RGBA", (red, green, blue, alpha))
            # 清空 PIL 读取到的文本元数据，防止重新写入 tEXt/iTXt chunk。
            clean_image.info.clear()
            buf = io.BytesIO()
            clean_image.save(buf, format="PNG", optimize=False)
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
    def save_b64_to_file(
        b64_data: str,
        save_dir: Path,
        prefix: str = "image",
    ) -> Optional[str]:
        """将 Base64 图片保存到指定目录。"""

        import uuid

        try:
            clean_b64 = b64_data.split(",", 1)[-1] if b64_data.startswith("data:") else b64_data
            image_bytes = base64.b64decode(clean_b64)
            if not image_bytes:
                return None
            save_dir.mkdir(parents=True, exist_ok=True)
            file_path = save_dir / f"{prefix}_{uuid.uuid4()}.png"
            file_path.write_bytes(image_bytes)
            return str(file_path)
        except Exception as error:
            logger.error(f"保存 Base64 图片失败: {error}", exc_info=True)
            return None

    @staticmethod
    def get_image_size_from_b64(b64_data: str) -> tuple[int, int]:
        """读取 Base64 图片的宽高，失败时返回零尺寸。"""

        try:
            clean_b64 = b64_data.split(",", 1)[-1] if b64_data.startswith("data:") else b64_data
            with Image.open(io.BytesIO(base64.b64decode(clean_b64))) as image:
                return image.size
        except Exception as error:
            logger.warning(f"读取图片尺寸失败: {error}")
            return 0, 0

    @staticmethod
    def downscale_image_b64(
        b64_data: str,
        max_pixels: int = 1_048_576,
        align: int = 64,
    ) -> tuple[str, int, int]:
        """按宽高比缩小 Base64 图片，并将尺寸对齐到指定粒度。"""

        try:
            clean_b64 = b64_data.split(",", 1)[-1] if b64_data.startswith("data:") else b64_data
            image_bytes = base64.b64decode(clean_b64)
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                if width * height <= max_pixels:
                    return clean_b64, width, height
                ratio = (max_pixels / (width * height)) ** 0.5
                new_width = max(align, int(width * ratio // align) * align)
                new_height = max(align, int(height * ratio // align) * align)
                while new_width * new_height > max_pixels:
                    if new_width >= new_height and new_width > align:
                        new_width -= align
                    elif new_height > align:
                        new_height -= align
                    else:
                        break
                resized = image.convert("RGB").resize(
                    (new_width, new_height),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                resized.save(output, format="PNG")
            return base64.b64encode(output.getvalue()).decode("utf-8"), new_width, new_height
        except Exception as error:
            logger.error(f"图片缩放失败: {error}", exc_info=True)
            return b64_data, 0, 0

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
