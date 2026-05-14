"""画图 Tool — 模型日常可见的轻量级画图入口。

此 Tool 是画图流程的第一步：
1. 模型日常只看到这个简单的 Tool（schema 小、参数少）
2. 模型调用 Tool 后，Tool 返回确认信息，同时系统暴露 action-draw_image
3. action-draw_image 的 description 中包含完整的提示词编写指南和预设
4. 模型根据 Action description 中的指南，调用 action-draw_image 执行实际生图

设计意图：
- 降低模型日常 schema 负担（不需要时不暴露复杂的 NovelAI 语法）
- Action 的 description 作为"说明书"，包含提示词语法、预设、可选 Vibe 等
- 用户可在 config 中配置的预设、角色标签等都注入到 Action description
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool
from src.app.plugin_system.types import ChatType

logger = get_logger("image_generator_plugin.draw_image_tool")


class DrawImageTool(BaseTool):
    """画图工具 — 当需要为用户生成图片时调用。

    适用场景：
    - 用户想要任何图片：「画个…」「生成一张…」「来张…」等
    - 用户想看 Bot 的照片/自拍/样子/外观
    - 对话中需要视觉内容辅助表达

    调用此工具后，系统会暴露 action-draw_image，
    其 description 中包含完整的 NovelAI 提示词编写指南。
    请根据指南编写提示词后调用 action-draw_image 执行生图。
    """

    tool_name: str = "draw_image"
    tool_description: str = (
        "为用户画一张图片，或者生成你自己的照片/自拍。\n"
        "当用户想要任何图片时调用：'画个...' '生成一张...' '来张...' 等。\n"
        "当用户想看你的照片/自拍/样子/外观时也调用此工具。\n"
        "调用后系统会暴露 action-draw_image，根据其说明编写提示词并调用即可生图。"
    )

    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        description: Annotated[
            str,
            "用自然语言简要描述想要画的内容（中文或英文均可）。"
            "包括主体、场景、风格、情绪等关键信息。",
        ],
    ) -> tuple[bool, str]:
        """确认画图请求，提示模型调用 action-draw_image。"""
        if not description.strip():
            return False, "请描述你想要画的内容"

        logger.info(f"画图 Tool 被调用 - 描述: {description}")

        return True, (
            f"画图请求已确认：{description}\n"
            f"请查看 action-draw_image 的说明，根据指南将描述转换为英文标签式提示词，"
            f"然后调用 action-draw_image 执行生图。"
        )
