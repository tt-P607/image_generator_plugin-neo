"""AI 导演工具 Action — 对图片进行风格变换或后处理。

6 种工具各自独立为单独的 Action，由配置开关控制是否注册：
- declutter：去杂物（清理多余元素、遮挡物和文字）
- bg-removal：精细抠图（去背景，输出透明 PNG）
- lineart：提取线稿
- sketch：转铅笔画（草图化）
- colorize：线稿上色（需要 prompt 描述颜色方案）
- emotion：改变人物表情（需要 prompt 描述表情）

通过 chat_stream 引用消息提取原图，调用 Service 层 director_tool 方法。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.types import ChatType

from .base_image_action import BaseImageAction

logger = get_logger("image_generator_plugin.director_tool_action")


class _BaseDirectorAction(BaseImageAction):
    """导演工具 Action 基类。

    子类需设置 ``_tool_type`` 和 ``_tool_display``。
    需要额外 prompt 的工具（colorize/emotion）设置 ``_needs_prompt = True``。
    """

    _tool_type: str = ""
    _tool_display: str = ""
    _needs_prompt: bool = False

    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        image_filename: Annotated[
            str,
            "Bot 自己生成的图片文件名（draw_image 时自定义的 output_filename）。"
            "填写后从 temp_images 目录加载该图片处理，无需引用消息。"
            "处理用户发送的图片时留空，通过引用消息自动提取。",
        ] = "",
        prompt: Annotated[
            str,
            "风格描述，可选。用于引导处理效果。",
        ] = "",
        defry: Annotated[
            int,
            "去模糊强度 0-5，默认 0。仅部分工具有效。",
        ] = 0,
    ) -> tuple[bool, str]:
        """执行导演工具处理。"""
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
            await send_text("需要先发一张图片，或提供 image_filename，我才能帮你处理哦", stream_id=self.chat_stream.stream_id)
            return False, "未找到图片"

        # 校验需要 prompt 的工具
        if self._needs_prompt and not prompt.strip():
            return False, f"{self._tool_display}工具需要提供 prompt 参数"

        service = self.get_service()
        if not service:
            return False, "图片生成服务不可用"

        chat_context = getattr(self.chat_stream, "context", None)
        user_id = chat_context.triggering_user_id if chat_context else ""

        logger.info(f"导演工具请求: {self._tool_type} ({self._tool_display}) - user={user_id}")

        from src.kernel.concurrency import get_task_manager
        import asyncio

        task_state: dict[str, bool] = {"cancelled": False}

        async def _core_task() -> tuple[bool, str]:
            try:
                success, msg, image_path = await service.director_tool(
                    tool_type=self._tool_type,
                    image_b64=image_b64,
                    user_id=str(user_id),
                    prompt=prompt.strip() or None,
                    defry=defry if defry else None,
                )
                if success and image_path:
                    success_result, image_msg = await self.read_and_send_image(
                        image_path,
                        success_message=f"[内部：已发送{self._tool_display}结果]",
                        keep_file=True,
                    )
                    if success_result:
                        from pathlib import Path as _Path
                        actual_filename = _Path(image_path).name
                        return True, f"[内部：已发送{self._tool_display}结果]（文件名: {actual_filename}）"
                    return success_result, image_msg
                logger.error(f"导演工具 {self._tool_type} 失败: {msg}")
                err_msg = f"{self._tool_display}失败: {msg}"
                if task_state["cancelled"]:
                    await send_text(err_msg, stream_id=self.chat_stream.stream_id)
                return False, err_msg
            except Exception as e:
                logger.error(f"导演工具后台异常: {e}", exc_info=True)
                err_msg = f"发生异常: {e}"
                if task_state["cancelled"]:
                    await send_text(f"{self._tool_display}失败: {err_msg}", stream_id=self.chat_stream.stream_id)
                return False, f"{self._tool_display}失败: {e}"

        tm = get_task_manager()
        task_info = tm.create_task(
            _core_task(),
            name=f"director_{self._tool_type}_{user_id}",
            metadata={
                "plugin": "image_generator_plugin-neo",
                "purpose": f"action_director_{self._tool_type}",
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
            return False, f"后台{self._tool_display}任务创建失败"

        try:
            return await asyncio.shield(task_info.task)
        except asyncio.CancelledError:
            task_state["cancelled"] = True
            logger.warning(f"导演工具 {self._tool_type} 任务被取消，进入后台保护执行")
            raise


class DeclutterAction(_BaseDirectorAction):
    """去杂物 — 清理图片中多余元素、遮挡物和文字。"""

    name: str = "director_declutter"
    description: str = (
        "对图片进行「去杂物」处理——清理图片中多余元素、遮挡物和文字。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "标准尺寸（≤1024×1024）内免费。"
    )
    _tool_type: str = "declutter"
    _tool_display: str = "去杂物"


class BgRemovalAction(_BaseDirectorAction):
    """精细抠图 — 去背景，输出透明 PNG。"""

    name: str = "director_bg_removal"
    description: str = (
        "对图片进行「抠图」处理——精细去除背景，输出透明 PNG。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "⚠️ 始终消耗 65~200 Anlas（根据图片尺寸浮动），不享受免费额度。"
    )
    _tool_type: str = "bg-removal"
    _tool_display: str = "抠图"


class LineartAction(_BaseDirectorAction):
    """提取线稿。"""

    name: str = "director_lineart"
    description: str = (
        "对图片进行「提取线稿」处理——从图片中提取线稿。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "标准尺寸（≤1024×1024）内免费。"
    )
    _tool_type: str = "lineart"
    _tool_display: str = "线稿"


class SketchAction(_BaseDirectorAction):
    """转铅笔画 — 草图化。"""

    name: str = "director_sketch"
    description: str = (
        "对图片进行「铅笔画」处理——将图片转为铅笔画风格。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "标准尺寸（≤1024×1024）内免费。"
    )
    _tool_type: str = "sketch"
    _tool_display: str = "铅笔画"


class ColorizeAction(_BaseDirectorAction):
    """线稿上色 — 需要 prompt 描述颜色方案。"""

    name: str = "director_colorize"
    description: str = (
        "对线稿图片进行「上色」处理——为线稿填充颜色。\n\n"
        "需要提供 prompt 参数描述颜色方案（如 'warm orange and blue tones'）。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "标准尺寸（≤1024×1024）内免费。"
    )
    _tool_type: str = "colorize"
    _tool_display: str = "上色"
    _needs_prompt: bool = True


class EmotionAction(_BaseDirectorAction):
    """改变人物表情 — 需要 prompt 描述表情。"""

    name: str = "director_emotion"
    description: str = (
        "对图片进行「改变表情」处理——改变人物的表情。\n\n"
        "需要提供 prompt 参数描述表情（如 'happy smile, closed eyes'）。\n\n"
        "**图片来源**：\n"
        "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
        "  - 处理用户发送的图片：引用该图片，留空 image_filename\n\n"
        "标准尺寸（≤1024×1024）内免费。"
    )
    _tool_type: str = "emotion"
    _tool_display: str = "表情"
    _needs_prompt: bool = True
