"""AI 局部重绘 Action。

模型给出比例坐标的重绘区域，插件生成矩形遮罩后调用引擎重绘，
遮罩之外的画面保持不变。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from ..engine import ImageResult, InpaintSpec
from ..media import image_ops
from .base import BaseImageAction

logger = get_logger("image_generator_plugin.inpaint_action")

DEFAULT_MASK_SIZE = 0.5
MIN_MASK_SIZE = 0.01
FALLBACK_IMAGE_SIZE = (1024, 1024)


class InpaintAction(BaseImageAction):
    """AI 局部重绘动作。"""

    name: str = "inpaint_image"
    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL
    description: str = (
        "对图片进行局部重绘——保留未指定区域，仅重绘指定部分。\n"
        "使用场景：修改图片中某个元素的样式、替换背景局部、修正细节等。\n\n"
        "**图片来源**：\n"
        "  - 处理用户发送的图片：从上下文 [图片(media_id)] 提取哈希值填入 media_id 参数\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n\n"
        "**遮罩区域参数**（mask_area）：\n"
        "  JSON 对象，指定矩形重绘区域，坐标为 0.0-1.0 比例值"
        "（与图片宽高无关，适用于任意画幅）：\n"
        '  {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}\n'
        "  - x, y：左上角坐标（0=最左/最上，1=最右/最下）\n"
        "  - w, h：宽度和高度比例（0.01-1.0）\n"
        "  注意：x/y/w/h 都是相对于整张图片的比例，不是像素值。\n"
        '  例如竖图（832×1216）重绘左上角 1/4 → {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}\n'
        '  例如横图（1216×832）重绘右侧 1/3 → {"x": 0.67, "y": 0.0, "w": 0.33, "h": 1.0}\n\n'
        "**重绘强度**（strength）：\n"
        "  0.01-1.0，越高越偏离原图。1.0 = 完全重绘该区域。\n"
        "  修小细节建议 0.3-0.5，大改建议 0.7-1.0。\n\n"
        "**⚠️ 重绘提示词规范**（保证画风一致性）：\n"
        "  NovelAI 局部重绘没有独立的区域提示词，content_description 是整张图的完整提示词。\n"
        "  必须传入完整提示词（原图 tag + 修改），而不是只传修改部分。\n"
        "  - 在原图完整 tag 基础上，仅对需要修改的部分进行增删，其余保持不变\n"
        "  - 例如换衣服：原图是 '1girl, white shirt, skirt, standing, looking at viewer'，\n"
        "    修改后传 '1girl, pink dress, frills, standing, looking at viewer'\n"
        "  - 画风标签（如 game cg）需保留，保持与原图一致\n"
        "  - 负面提示词中排除可能渗透的默认饰品（如 hair ornament, wings, cape 等）\n\n"
        "**文件名规范**：\n"
        "  出图成功后返回值包含文件名，后续可通过 image_filename 引用此图片。\n"
        "  文件名格式：仅英文/数字/下划线，不含扩展名。"
    )

    async def execute(
        self,
        content_description: Annotated[
            str,
            "重绘后整张图片的完整英文 NovelAI 标签描述。"
            "需在原图标签基础上增删，而不是只写修改部分。",
        ],
        mask_area: Annotated[
            str,
            '重绘区域 JSON，格式 {"x": float, "y": float, "w": float, "h": float}。'
            "x/y 为左上角坐标（0.0-1.0），w/h 为宽高比例（0.0-1.0）。"
            '例如重绘右半部分：{"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}',
        ],
        strength: Annotated[
            float,
            "重绘强度 0.01-1.0。越高越偏离原图。修小细节建议 0.3-0.5，大改建议 0.7-1.0。",
        ] = 0.7,
        negative_prompt: Annotated[
            str,
            "场景专属额外排除词，英文逗号分隔。",
        ] = "",
        media_id: Annotated[
            str,
            "待处理图片的媒体 ID。用户发送的图片在上下文中以 "
            "[图片(media_id)] 出现，从占位符括号内提取哈希值填入此参数。"
            "处理 Bot 自己生成的图片时留空，改用 image_filename。",
        ] = "",
        image_filename: Annotated[
            str,
            "Bot 自己生成的图片文件名（draw_image 时自定义的 output_filename）。"
            "填写后从产图目录加载该图片进行局部重绘，无需引用消息。"
            "处理用户发送的图片时留空，改用 media_id。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行局部重绘。"""
        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"

        area = _parse_mask_area(mask_area)
        if isinstance(area, str):
            return False, area

        image_b64 = await self.resolve_source_image(image_filename, media_id)
        if not image_b64:
            hint = (
                f"找不到 media_id={media_id} 对应的图片"
                if media_id.strip()
                else f"找不到文件名为 '{image_filename}' 的图片"
                if image_filename.strip()
                else "需要先发一张图片（提供 media_id 或 image_filename），我才能帮你局部重绘哦"
            )
            await self.notify(hint)
            return False, hint

        width, height = image_ops.read_image_size(image_b64)
        if not width or not height:
            width, height = FALLBACK_IMAGE_SIZE

        # 遮罩必须与最终发送给 API 的画幅一致，因此先完成缩放/对齐再生成遮罩。
        # NovelAI 要求画幅为 64 的倍数，未对齐的图片（如 1080x508）会导致 500。
        image_b64, new_width, new_height = image_ops.downscale_to_free_tier(image_b64)
        if (new_width, new_height) != (width, height):
            logger.info(f"局部重绘原图自动缩放: {width}x{height} → {new_width}x{new_height}")
            width, height = new_width, new_height

        mask_x, mask_y, mask_w, mask_h = area
        mask = image_ops.build_rect_mask(width, height, mask_x, mask_y, mask_w, mask_h)
        logger.info(
            f"局部重绘区域 ({mask_x:.2f},{mask_y:.2f}) 尺寸 {mask_w:.2f}x{mask_h:.2f}"
            f" | 画幅 {width}x{height} | strength={strength}"
        )

        spec = InpaintSpec(
            prompt=content_description,
            source_image=image_b64,
            mask=mask,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            strength=strength,
        )

        async def _work() -> ImageResult:
            return await engine.inpaint(spec)

        return await self.run_in_background(
            _work,
            task_name=f"inpaint_action_{self.triggering_user_id}",
            purpose="action_inpaint",
            success_message="[内部：已发送局部重绘图片]",
            error_prefix="局部重绘失败",
        )


def _parse_mask_area(raw: str | dict[str, Any]) -> tuple[float, float, float, float] | str:
    """解析并夹紧遮罩区域参数。

    Args:
        raw: JSON 字符串或已解析的对象

    Returns:
        (x, y, w, h) 比例值，或描述问题的错误文本
    """
    if isinstance(raw, dict):
        area: Any = raw
    else:
        try:
            area = json.loads(raw)
        except json.JSONDecodeError as error:
            return f"mask_area 不是合法 JSON：{error}"

    if not isinstance(area, dict):
        return "mask_area 必须是 JSON 对象"

    try:
        x = float(area.get("x", 0.0))
        y = float(area.get("y", 0.0))
        w = float(area.get("w", DEFAULT_MASK_SIZE))
        h = float(area.get("h", DEFAULT_MASK_SIZE))
    except (TypeError, ValueError):
        return "mask_area 的 x/y/w/h 必须是数字"

    return (
        max(0.0, min(1.0, x)),
        max(0.0, min(1.0, y)),
        max(MIN_MASK_SIZE, min(1.0, w)),
        max(MIN_MASK_SIZE, min(1.0, h)),
    )
