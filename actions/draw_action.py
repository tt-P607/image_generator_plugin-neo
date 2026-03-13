"""AI 画图 Action — 为用户生成任何图片。

当用户想要任何图片时调用此 Action，包括但不限于：
「画个…」「生成一张…」「来张…」等。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.types import ChatType

from .base_image_action import BaseImageAction

logger = get_logger("image_generator_plugin.draw_action")


class DrawAction(BaseImageAction):
    """AI 画图动作 — 为用户画任何想要的图片。

    使用要求（供 LLM 参考）：
    - **主动调用**：当用户想要图片时，积极使用此功能。
    - **绝对禁止中文**：content_description 必须 100% 使用英文 NovelAI 标签。
    - **核心格式**：逗号分隔的英文标签，采用 NovelAI 标准格式（小写字母，下划线连接复合词）。
      标签顺序：质量/风格 > 主体/动作 > 细节/环境。
    - **高级加权**：使用 `n::tag::` 语法。n>1 提升权重（如 `1.3::red hair::`），n<1 降低。
    - **标签转换**：将中文概念拆解为具体英文标签。人物需含数量+特征+服装+表情+动作。
    - **角色标注**：特定角色用 `character_name (source)` 格式。
    - **画质标签**：建议包含 `masterpiece, best quality, highres`。
    - **画幅选择**：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024。
    """

    action_name: str = "draw_image"
    action_description: str = (
        "为用户画一张图片。当用户想要任何图片时就使用此功能，"
        "包括但不限于：'画个...' '生成一张...' '来张...' 等。"
    )

    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        content_description: Annotated[
            str,
            "图片内容的 NovelAI 英文标签。必须是英文逗号分隔的标签，"
            "详细描述主体、场景、动作、氛围、光线等所有视觉元素。",
        ],
        resolution: Annotated[
            str,
            "图片画幅尺寸。横图用 '1216x832'，竖图用 '832x1216'，方图用 '1024x1024'。"
            "根据内容主体形态选择。",
        ] = "1024x1024",
        negative_prompt: Annotated[
            str,
            "特殊场景的负面提示词，英文逗号分隔的标签，用于排除不想要的元素。"
            "如无特殊需求可留空。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行画图动作。"""
        if not content_description:
            logger.warning("画图动作未提供内容描述")
            return False, "画什么呢？请告诉我你想要的图片内容~"

        width, height = self._parse_resolution(resolution, default="1024x1024")

        prompt = self._build_prompt(content_description)
        logger.info(f"AI 画图 - 提示词: {prompt}, 画幅: {width}x{height}")

        if negative_prompt:
            logger.info(f"用户负面提示词: {negative_prompt}")

        return await self.generate_and_send_image(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            success_message="[内部：已发送画作]",
            error_prefix="画画失败了",
        )

    def _build_prompt(self, content_tags: str) -> str:
        """构建图片生成提示词。

        Args:
            content_tags: AI 已转换为英文标签的内容描述

        Returns:
            完整的提示词字符串
        """
        quality_tags = "masterpiece, best quality, ultra detailed, high resolution, illustration"
        return f"{quality_tags}, {content_tags}, beautiful lighting, depth of field"
