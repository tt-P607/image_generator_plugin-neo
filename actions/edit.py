"""AI 图生图（整图重绘）Action。

以一张已有图片为底，按提示词整图重绘。与 inpaint_image 的区别：
不需要遮罩，重绘范围是整张画面，strength 控制偏离原图的程度。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from ..engine import GenerationSpec, ImageResult
from ..media import image_ops
from .base import BaseImageAction

logger = get_logger("image_generator_plugin.edit_action")

FALLBACK_IMAGE_SIZE = (1024, 1024)


class EditImageAction(BaseImageAction):
    """AI 图生图动作 — 基于已有图片整图重绘。"""

    name: str = "edit_image"
    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL
    description: str = (
        "图生图（Img2Img）——以一张已有图片为底，按提示词对整张画面重绘。\n"
        "使用场景：改变画风、微调整体内容、把草图/照片重绘成插画等。\n"
        "与 inpaint_image 的区别：不需要指定区域，整张图都会参与重绘。\n\n"
        "**图片来源**：\n"
        "  - 处理用户发送的图片：从上下文 [图片(media_id)] 提取哈希值填入 media_id 参数\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n\n"
        "**重绘强度**（strength）：\n"
        "  0.01-1.0，越高越偏离原图。\n"
        "  轻微调整建议 0.3-0.5，风格转换建议 0.5-0.7，大改建议 0.7-0.9。\n\n"
        "**提示词规范**：\n"
        "  content_description 描述重绘后整张图的内容（英文 NovelAI 标签），\n"
        "  建议在原图内容基础上增删，保留想维持的元素标签。\n\n"
        "**文件名规范**：\n"
        "  出图成功后返回值包含文件名，后续可通过 image_filename 引用此图片。\n"
        "  文件名格式：仅英文/数字/下划线，不含扩展名。"
    )

    async def execute(
        self,
        content_description: Annotated[
            str,
            "重绘后整张图片的英文 NovelAI 标签描述。"
            "建议在原图内容基础上增删，保留想维持的元素标签。",
        ],
        strength: Annotated[
            float,
            "重绘强度 0.01-1.0。越高越偏离原图。"
            "轻微调整 0.3-0.5，风格转换 0.5-0.7，大改 0.7-0.9。",
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
            "填写后从产图目录加载该图片进行图生图，无需引用消息。"
            "处理用户发送的图片时留空，改用 media_id。",
        ] = "",
        output_filename: Annotated[
            str,
            "输出文件名（不含扩展名，仅英文/数字/下划线）。"
            "留空则使用随机文件名。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行图生图。"""
        if not content_description.strip():
            return False, "要怎么改呢？请告诉我重绘后的图片内容~"

        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"

        image_b64 = await self.resolve_source_image(image_filename, media_id)
        if not image_b64:
            hint = (
                f"找不到 media_id={media_id} 对应的图片"
                if media_id.strip()
                else f"找不到文件名为 '{image_filename}' 的图片"
                if image_filename.strip()
                else "需要先发一张图片（提供 media_id 或 image_filename），我才能帮你图生图哦"
            )
            await self.notify(hint)
            return False, hint

        width, height = image_ops.read_image_size(image_b64)
        if not width or not height:
            width, height = FALLBACK_IMAGE_SIZE

        logger.info(
            f"图生图 - 提示词: {content_description}"
            f" | 画幅 {width}x{height} | strength={strength}"
        )

        spec = GenerationSpec(
            prompt=content_description,
            user_id=self.triggering_user_id,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            source_image=image_ops.strip_data_url_prefix(image_b64),
            strength=max(0.01, min(1.0, strength)),
        )

        async def _work() -> ImageResult:
            return await engine.generate(spec)

        return await self.run_in_background(
            _work,
            task_name=f"edit_action_{self.triggering_user_id}",
            purpose="action_edit",
            success_message="[内部：已发送图生图结果]",
            error_prefix="图生图失败",
            output_filename=output_filename,
        )
