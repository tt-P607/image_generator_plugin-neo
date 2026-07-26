"""AI 局部重绘 Action — 对已有图片进行局部修改重绘。

LLM 通过指定重绘区域坐标和提示词，让插件生成矩形遮罩，
仅重绘遮罩覆盖的部分，保留其余区域不变。

设计意图：
- LLM 提供 mask_area JSON 坐标，插件用 PIL 生成 RGBA 遮罩 PNG
- 通过 chat_stream 引用消息提取原图 base64
- 双渠道支持：official 使用 action=infill，gateway 使用 /v1/images/inpainting
"""

from __future__ import annotations

import json
from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.types import ChatType

from .base_image_action import BaseImageAction

logger = get_logger("image_generator_plugin.inpaint_action")


class InpaintAction(BaseImageAction):
    """AI 局部重绘动作 — 对已有图片进行局部修改重绘。

    LLM 提供重绘区域坐标和提示词，插件生成遮罩后调用局部重绘 API。
    仅重绘遮罩覆盖的区域，其余部分保持不变。
    """

    name: str = "inpaint_image"
    associated_types: list[str] = ["image"]
    description: str = (
        "对图片进行局部重绘——保留未指定区域，仅重绘指定部分。\n"
        "使用场景：修改图片中某个元素的样式、替换背景局部、修正细节等。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "**遮罩区域参数**（mask_area）：\n"
        "  JSON 对象，指定矩形重绘区域，坐标为 0.0-1.0 比例值（与图片宽高无关，适用于任意画幅）：\n"
        "  {\"x\": 0.1, \"y\": 0.2, \"w\": 0.3, \"h\": 0.4}\n"
        "  - x, y：左上角坐标（0=最左/最上，1=最右/最下）\n"
        "  - w, h：宽度和高度比例（0.01-1.0）\n"
        "  注意：x/y/w/h 都是相对于整张图片的比例，不是像素值。\n"
        "  例如竖图（832×1216）重绘左上角 1/4 → {\"x\": 0.0, \"y\": 0.0, \"w\": 0.5, \"h\": 0.5}\n"
        "  例如横图（1216×832）重绘右侧 1/3 → {\"x\": 0.67, \"y\": 0.0, \"w\": 0.33, \"h\": 1.0}\n\n"
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

    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        content_description: Annotated[
            str,
            "重绘区域的英文 NovelAI 标签描述，描述你希望在指定区域生成什么内容。"
            "例如：'blue sky, white clouds' 或 'red dress, frills'。",
        ],
        mask_area: Annotated[
            str,
            "重绘区域 JSON，格式 {\"x\": float, \"y\": float, \"w\": float, \"h\": float}。"
            "x/y 为左上角坐标（0.0-1.0），w/h 为宽高比例（0.0-1.0）。"
            "例如重绘右半部分：{\"x\": 0.5, \"y\": 0.0, \"w\": 0.5, \"h\": 1.0}",
        ],
        strength: Annotated[
            float,
            "重绘强度 0.01-1.0。越高越偏离原图。修小细节建议 0.3-0.5，大改建议 0.7-1.0。",
        ] = 0.7,
        negative_prompt: Annotated[
            str,
            "场景专属额外排除词，英文逗号分隔。",
        ] = "",
        image_filename: Annotated[
            str,
            "Bot 自己生成的图片文件名（draw_image 时自定义的 output_filename）。"
            "填写后从 temp_images 目录加载该图片进行局部重绘，无需引用消息。"
            "处理用户发送的图片时留空，通过引用消息自动提取。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行局部重绘。"""
        # 提取原图：优先用文件名，其次从引用消息提取
        image_b64 = None
        if image_filename.strip():
            image_b64 = self._load_image_by_filename(image_filename)
            if not image_b64:
                await send_text(f"找不到文件名为 '{image_filename}' 的图片", stream_id=self.chat_stream.stream_id)
                return False, f"文件 {image_filename} 不存在"
        if not image_b64:
            image_b64 = await self._extract_image_from_reply()
        if not image_b64:
            await send_text("需要先发一张图片，或提供 image_filename，我才能帮你局部重绘哦", stream_id=self.chat_stream.stream_id)
            return False, "未找到图片"

        # 解析 mask_area JSON
        try:
            area = json.loads(mask_area) if isinstance(mask_area, str) else mask_area
        except json.JSONDecodeError as e:
            return False, f"mask_area 不是合法 JSON：{e!s}"

        if not isinstance(area, dict):
            return False, "mask_area 必须是 JSON 对象"

        try:
            mx = float(area.get("x", 0.0))
            my = float(area.get("y", 0.0))
            mw = float(area.get("w", 0.5))
            mh = float(area.get("h", 0.5))
        except (TypeError, ValueError):
            return False, "mask_area 的 x/y/w/h 必须是数字"

        # 限制范围
        mx = max(0.0, min(1.0, mx))
        my = max(0.0, min(1.0, my))
        mw = max(0.01, min(1.0, mw))
        mh = max(0.01, min(1.0, mh))

        # 读取原图尺寸
        from ..utils.image_utils import ImageUtils
        orig_w, orig_h = ImageUtils.get_image_size_from_b64(image_b64)
        if not orig_w or not orig_h:
            orig_w, orig_h = 1024, 1024

        # 如果原图超过 1M 像素，等比缩放到 Opus 免费范围
        # 遮罩必须基于最终发送给 API 的目标尺寸生成，否则尺寸不匹配
        target_w, target_h = orig_w, orig_h
        if orig_w * orig_h > 1048576:
            image_b64, target_w, target_h = ImageUtils.downscale_image_b64(
                image_b64, max_pixels=1048576, align=64
            )
            logger.info(
                f"局部重绘原图自动缩放: {orig_w}x{orig_h} → {target_w}x{target_h}"
            )

        # 基于目标尺寸生成矩形遮罩（比例坐标 × 目标宽高）
        mask_b64 = ImageUtils.generate_rect_mask(target_w, target_h, mx, my, mw, mh)
        logger.info(
            f"局部重绘 - 区域: ({mx:.2f},{my:.2f})-({mx+mw:.2f},{my+mh:.2f}) "
            f"图片: {target_w}x{target_h} | strength={strength}"
        )

        # 调用 Service
        service = self.get_service()
        if not service:
            return False, "图片生成服务不可用"

        chat_context = getattr(self.chat_stream, "context", None)
        user_id = chat_context.triggering_user_id if chat_context else ""

        from src.kernel.concurrency import get_task_manager
        import asyncio

        task_state: dict[str, bool] = {"cancelled": False}

        async def _core_task() -> tuple[bool, str]:
            try:
                success, msg, image_path = await service.inpaint_image(
                    prompt=content_description,
                    image_b64=image_b64,
                    mask_b64=mask_b64,
                    user_id=str(user_id),
                    negative_prompt=negative_prompt or None,
                    width=target_w,
                    height=target_h,
                    strength=strength,
                )
                if success and image_path:
                    success_result, image_msg = await self.read_and_send_image(
                        image_path,
                        success_message="[内部：已发送局部重绘图片]",
                        keep_file=True,
                    )
                    if success_result:
                        from pathlib import Path as _Path
                        actual_filename = _Path(image_path).name
                        return True, f"[内部：已发送局部重绘图片]（文件名: {actual_filename}）"
                    return success_result, image_msg
                logger.error(f"局部重绘失败: {msg}")
                err_msg = f"局部重绘失败: {msg}"
                if task_state["cancelled"]:
                    await send_text(err_msg, stream_id=self.chat_stream.stream_id)
                return False, err_msg
            except Exception as e:
                logger.error(f"局部重绘后台异常: {e}", exc_info=True)
                err_msg = f"发生异常: {e}"
                if task_state["cancelled"]:
                    await send_text(f"局部重绘失败: {err_msg}", stream_id=self.chat_stream.stream_id)
                return False, f"局部重绘失败: {e}"

        tm = get_task_manager()
        task_info = tm.create_task(
            _core_task(),
            name=f"inpaint_action_{user_id}",
            metadata={
                "plugin": "image_generator_plugin-neo",
                "purpose": "action_inpaint",
                "stream_id": self.chat_stream.stream_id,
            },
        )
        register_task = getattr(self.plugin, "register_background_task", None)
        discard_task = getattr(self.plugin, "discard_background_task", None)
        if callable(register_task):
            register_task(task_info.task_id)
        if task_info.task is not None and callable(discard_task):
            task_info.task.add_done_callback(
                lambda _task, task_id=task_info.task_id: discard_task(task_id)
            )

        if task_info.task is None:
            return False, "后台局部重绘任务创建失败"

        try:
            return await asyncio.shield(task_info.task)
        except asyncio.CancelledError:
            task_state["cancelled"] = True
            logger.warning("局部重绘任务被取消，进入后台保护执行")
            raise
