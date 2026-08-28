"""图片编解码与几何处理。

纯函数集合，仅依赖 Pillow，不涉及框架与网络。
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

from PIL import Image

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("image_generator_plugin.image_ops")

DIRECTOR_REF_SIZE = (1024, 1536)
FREE_TIER_MAX_PIXELS = 1_048_576
SIZE_ALIGNMENT = 64


def strip_data_url_prefix(b64_data: str) -> str:
    """去掉 data URL 前缀，返回纯 base64 内容。

    Args:
        b64_data: 可能带 ``data:image/png;base64,`` 前缀的字符串

    Returns:
        纯 base64 字符串
    """
    if b64_data.startswith("data:"):
        return b64_data.split(",", 1)[-1]
    return b64_data


def encode_file(path: Path) -> str:
    """将文件内容编码为 base64。

    Args:
        path: 文件路径

    Returns:
        base64 字符串
    """
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def read_image_size(b64_data: str) -> tuple[int, int]:
    """读取 base64 图片的宽高。

    Args:
        b64_data: 图片 base64

    Returns:
        (宽, 高)，解析失败时返回 (0, 0)
    """
    try:
        raw = base64.b64decode(strip_data_url_prefix(b64_data))
        with Image.open(io.BytesIO(raw)) as image:
            return image.size
    except (OSError, ValueError) as error:
        logger.warning(f"读取图片尺寸失败: {error}")
        return 0, 0


def strip_png_metadata(image_bytes: bytes) -> bytes:
    """剥离 NovelAI 生成图片中的全部元数据。

    NovelAI 的元数据写入两个区域：

    1. **标准 PNG 文本块（tEXt / iTXt）**：包含 Software、Source、Title、Comment
       等字段，Comment 中以 JSON 存储完整生成参数（提示词、种子、采样器等）。
       通过清除 Pillow 的 info 字典后重新保存，移除所有文本块。

    2. **Alpha 通道隐写区（Stealth PNGInfo）**：生成参数和模型签名哈希
       嵌入 Alpha 通道的最低有效位（LSB）中，作为标准文本块被擦除后的
       容错备份。通过 ``alpha & 0xFE`` 将每个像素 Alpha 通道最低位置 0，
       彻底清除 LSB 隐写痕迹（prompt + 签名哈希），透明度最大变化仅 1
       （255→254），人眼不可分辨。

    本地保存的原图不受影响，剥离仅在发送给用户前执行。

    Args:
        image_bytes: 原始 PNG 字节

    Returns:
        清理后的 PNG 字节
    """
    with Image.open(io.BytesIO(image_bytes)) as source:
        has_alpha = "A" in source.getbands()
        clean = source.convert("RGBA") if has_alpha else source.convert("RGB")
        if clean.mode == "RGBA":
            red, green, blue, alpha = clean.split()
            # alpha & 0xFE 清零最低位，彻底破坏 LSB 隐写，透明度无损。
            alpha = alpha.point(_clear_alpha_lsb)
            clean = Image.merge("RGBA", (red, green, blue, alpha))
        # 清除 tEXt/iTXt/zTXt 块（NovelAI 的 Comment/Source/Software 等）。
        clean.info.clear()
        buffer = io.BytesIO()
        # optimize=False 防止编码器重新注入辅助信息。
        clean.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()


def downscale_to_free_tier(
    b64_data: str,
    *,
    max_pixels: int = FREE_TIER_MAX_PIXELS,
    align: int = SIZE_ALIGNMENT,
) -> tuple[str, int, int]:
    """等比缩小图片到免费像素范围，并对齐尺寸粒度。

    NovelAI 要求画幅必须是 64 的倍数，因此即使像素总数未超限，
    宽高未对齐时也会缩放到最近的对齐尺寸，避免服务端 500。

    Args:
        b64_data: 图片 base64
        max_pixels: 像素总数上限
        align: 尺寸对齐粒度

    Returns:
        (缩放后 base64, 宽, 高)；无需处理时原样返回
    """
    clean = strip_data_url_prefix(b64_data)
    raw = base64.b64decode(clean)
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if (
            width * height <= max_pixels
            and width % align == 0
            and height % align == 0
        ):
            return clean, width, height

        ratio = min(1.0, (max_pixels / (width * height)) ** 0.5)
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
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")

    return base64.b64encode(buffer.getvalue()).decode("utf-8"), new_width, new_height


def fit_for_director_reference(b64_data: str) -> str:
    """将图片缩放并黑边填充到精密参考所需的 1024x1536 PNG。

    Args:
        b64_data: 原始图片 base64

    Returns:
        处理后的 base64
    """
    raw = base64.b64decode(strip_data_url_prefix(b64_data))
    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGB")
        target_width, target_height = DIRECTOR_REF_SIZE
        source_ratio = image.width / image.height
        target_ratio = target_width / target_height

        if source_ratio > target_ratio:
            new_width = target_width
            new_height = int(target_width / source_ratio)
        else:
            new_height = target_height
            new_width = int(target_height * source_ratio)

        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", DIRECTOR_REF_SIZE, (0, 0, 0))
        canvas.paste(
            resized,
            ((target_width - new_width) // 2, (target_height - new_height) // 2),
        )
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")

    return base64.b64encode(buffer.getvalue()).decode("utf-8")


MASK_BLOCK = 8


def _clear_alpha_lsb(value: int) -> int:
    """清零 8 位 Alpha 值的最低位。

    Args:
        value: 像素 Alpha 分量

    Returns:
        最低位被置 0 后的值
    """
    return value & 0xFE


def _invert_alpha(value: int) -> int:
    """反转 8 位 Alpha 值。

    Args:
        value: 像素 Alpha 分量

    Returns:
        按位取反后的值
    """
    return 255 - value


def build_rect_mask(
    width: int,
    height: int,
    x_ratio: float,
    y_ratio: float,
    width_ratio: float,
    height_ratio: float,
) -> str:
    """生成局部重绘所需的矩形遮罩。

    遮罩为 RGBA PNG，白色不透明区域参与重绘，其余区域保持原图。
    矩形边界对齐到 8×8 latent 块网格——NovelAI VAE 按 8×8 块处理遮罩，
    边缘切在块中间会导致该块被半重绘，渲染成灰色锯齿边。

    Args:
        width: 目标图片宽度
        height: 目标图片高度
        x_ratio: 重绘区左上角横向比例
        y_ratio: 重绘区左上角纵向比例
        width_ratio: 重绘区宽度比例
        height_ratio: 重绘区高度比例

    Returns:
        遮罩 PNG 的 base64
    """
    mask = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    # 左上角向下取整、右下角向上取整到 8px 块边界，保证选区完整覆盖且块对齐。
    left = int(width * x_ratio) // MASK_BLOCK * MASK_BLOCK
    top = int(height * y_ratio) // MASK_BLOCK * MASK_BLOCK
    right = min(
        width,
        -(-(int(width * x_ratio) + int(width * width_ratio)) // MASK_BLOCK)
        * MASK_BLOCK,
    )
    bottom = min(
        height,
        -(-(int(height * y_ratio) + int(height * height_ratio)) // MASK_BLOCK)
        * MASK_BLOCK,
    )
    if right > left and bottom > top:
        region = Image.new("RGBA", (right - left, bottom - top), (255, 255, 255, 255))
        mask.paste(region, (left, top))

    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def invert_mask_alpha(b64_data: str) -> str:
    """按 OpenAI 兼容语义反转蒙版透明度（透明=重绘）。

    插件内部的蒙版约定是白色不透明区域参与重绘；新版 OpenAI 兼容网关要求
    蒙版透明区域参与重绘、不透明区域保留，转发前需要交换 Alpha 通道。

    Args:
        b64_data: 蒙版 PNG 的 base64

    Returns:
        Alpha 反转后的蒙版 PNG base64
    """
    raw = base64.b64decode(strip_data_url_prefix(b64_data))
    with Image.open(io.BytesIO(raw)) as source:
        red, green, blue, alpha = source.convert("RGBA").split()
        inverted = alpha.point(_invert_alpha)
        flipped = Image.merge("RGBA", (red, green, blue, inverted))
        buffer = io.BytesIO()
        flipped.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def extract_first_image_from_zip(zip_bytes: bytes) -> bytes | None:
    """从 ZIP 响应中取出第一张图片。

    Args:
        zip_bytes: ZIP 文件字节

    Returns:
        图片字节，未找到时返回 None
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            if entry.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return archive.read(entry)
    return None


def is_zip_payload(data: bytes) -> bool:
    """判断响应字节是否为 ZIP 包。

    Args:
        data: 响应字节

    Returns:
        是否以 ZIP 魔数开头
    """
    return data[:4] == b"PK\x03\x04"
