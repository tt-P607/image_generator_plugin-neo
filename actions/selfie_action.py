"""AI 自拍生成 Action。

当用户想看 Bot 的照片/自拍/样子/外观时调用，使用内置角色特征。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.types import ChatType

from .base_image_action import BaseImageAction

if TYPE_CHECKING:
    from ..services.image_service import ImageGeneratorService

logger = get_logger("image_generator_plugin.selfie_action")


class GenerateSelfieAction(BaseImageAction):
    """AI 自拍生成 Action。

    当用户想看 Bot 的照片、自拍、样子、外观时使用。
    配置文件中有角色特征锚定（character_prompt），Action 参数无需重复基本外观。

    使用要求（供 LLM 参考）：
    - **触发条件**：仅当用户想看【你】的照片/自拍/样子/外观时调用。
    - **角色锚定**：配置文件的 character_prompt 已定义你的角色特征，参数中无需重复基本外观。
    - **绝对禁止中文**：所有参数必须 100% 使用英文 NovelAI 标签。
    - **核心格式**：逗号分隔的英文标签，NovelAI 标准格式。
    - **高级加权**：`n::tag::` 语法，重点用于强调动作、情绪和光影。
    - **画幅选择**：人物竖图 832x1216，风景横图 1216x832，头像方图 1024x1024。
    - **与 draw_image 区别**：draw_image 画任意内容，这个是画【你自己】。
    """

    action_name: str = "generate_selfie"
    action_description: str = (
        "生成【你自己】的照片发给用户。当用户想看你的照片、自拍、样子、外观时使用。"
        "配置文件中有你的角色特征锚定，参数中无需重复基本外观。\n\n"
        "【提示词编写规范】\n"
        "- **触发条件**：仅当用户想看【你】的照片/自拍/样子/外观时调用，与 draw_image 的区别是画的是你自己。\n"
        "- **绝对禁止中文**：所有参数必须 100% 使用英文 NovelAI 标签。\n"
        "- **核心格式**：逗号分隔的英文标签，NovelAI 标准格式。\n"
        "- **高级加权**：`n::tag::` 语法，重点用于强调动作、情绪和光影。\n"
        "- **画幅选择**：人物竖图 832x1216，风景横图 1216x832，头像方图 1024x1024。"
    )

    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        scene_description: Annotated[
            str,
            "场景和氛围的 NovelAI 英文标签。必须是英文逗号分隔的标签，"
            "包括地点、时间、光线等元素。",
        ],
        pose_or_action: Annotated[
            str,
            "姿势和动作的 NovelAI 英文标签。必须是英文逗号分隔的标签，"
            "描述肢体动作、表情、视线方向等。",
        ],
        resolution: Annotated[
            str,
            "图片画幅尺寸。竖图用 '832x1216'（人物肖像）、横图用 '1216x832'（风景/多人）、"
            "方图用 '1024x1024'（头像特写）。",
        ] = "832x1216",
        mood: Annotated[
            str,
            "情绪氛围的 NovelAI 英文标签。描述情感状态和气氛。可选。",
        ] = "",
        negative_prompt: Annotated[
            str,
            "特殊场景的负面提示词，英文逗号分隔的标签。如无特殊需求可留空。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行自拍动作。"""
        service = self.get_service()
        if not service:
            return False, "图片生成服务不可用"

        width, height = self._parse_resolution(resolution, default="832x1216")

        prompt = self._build_selfie_prompt(service, scene_description, pose_or_action, mood)
        logger.info(f"AI 自拍生成 - 提示词: {prompt}, 画幅: {width}x{height}")

        if negative_prompt:
            logger.info(f"用户负面提示词: {negative_prompt}")

        return await self.generate_and_send_image(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            success_message="[内部：已发送你的照片]",
            error_prefix="拍照失败",
        )

    def _build_selfie_prompt(
        self,
        service: "ImageGeneratorService",
        scene_tags: str,
        pose_tags: str,
        mood_tags: str,
    ) -> str:
        """构建自拍提示词。

        Args:
            service: 图片生成服务（用于获取 character_prompt 配置）
            scene_tags: 场景描述标签
            pose_tags: 姿势动作标签
            mood_tags: 情绪氛围标签

        Returns:
            完整的提示词字符串
        """
        character_base = service.character_prompt
        if not character_base:
            character_base = "1girl, beautiful detailed eyes, long pink hair, blue eyes, elf ears"

        scene_part = f"{scene_tags}, {pose_tags}" if scene_tags and pose_tags else ""
        mood_part = f", {mood_tags}" if mood_tags else ""
        quality_tags = "masterpiece, best quality, ultra detailed, high resolution, illustration"

        return f"{quality_tags}, {character_base}, {scene_part}{mood_part}, beautiful lighting, depth of field"
