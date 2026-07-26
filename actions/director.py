"""AI 导演工具 Action。

6 种工具各自注册为独立 Action，由配置开关控制是否启用：
declutter（去杂物）、bg-removal（抠图）、lineart（线稿）、
sketch（铅笔画）、colorize（上色）、emotion（改表情）。
"""

from __future__ import annotations

from typing import Annotated, ClassVar

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from ..engine import DirectorToolSpec, DirectorToolType, ImageResult
from ..media import image_ops
from .base import BaseImageAction

logger = get_logger("image_generator_plugin.director_action")

IMAGE_SOURCE_HINT = (
    "**图片来源**：\n"
    "  - 处理 Bot 自己生成的图片：填写 image_filename 参数\n"
    "  - 处理用户发送的图片：引用该图片，留空 image_filename"
)
FREE_TIER_HINT = "标准尺寸（≤1024×1024）内免费。"
FALLBACK_IMAGE_SIZE = (1024, 1024)


class BaseDirectorAction(BaseImageAction):
    """导演工具 Action 基类。

    子类需声明 ``tool_type`` 与 ``tool_display``；
    需要额外 prompt 的工具（colorize / emotion）额外声明 ``needs_prompt``。
    """

    tool_type: ClassVar[DirectorToolType]
    tool_display: ClassVar[str] = ""
    needs_prompt: ClassVar[bool] = False

    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        image_filename: Annotated[
            str,
            "Bot 自己生成的图片文件名（draw_image 时自定义的 output_filename）。"
            "填写后从产图目录加载该图片处理，无需引用消息。"
            "处理用户发送的图片时留空，通过引用消息自动提取。",
        ] = "",
        prompt: Annotated[
            str,
            "风格描述，可选。上色与改表情工具必填，用于引导处理效果。",
        ] = "",
        defry: Annotated[
            int,
            "去模糊强度 0-5，默认 0。仅上色与改表情工具有效。",
        ] = 0,
    ) -> tuple[bool, str]:
        """执行导演工具处理。"""
        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"

        if self.needs_prompt and not prompt.strip():
            return False, f"{self.tool_display}工具需要提供 prompt 参数"

        image_b64 = await self.resolve_source_image(image_filename)
        if not image_b64:
            hint = (
                f"找不到文件名为 '{image_filename}' 的图片"
                if image_filename.strip()
                else "需要先发一张图片，或提供 image_filename，我才能帮你处理哦"
            )
            await self.notify(hint)
            return False, hint

        width, height = image_ops.read_image_size(image_b64)
        if not width or not height:
            width, height = FALLBACK_IMAGE_SIZE

        logger.info(
            f"导演工具请求: {self.tool_type} ({self.tool_display})"
            f" - user={self.triggering_user_id}"
        )

        spec = DirectorToolSpec(
            tool_type=self.tool_type,
            source_image=image_ops.strip_data_url_prefix(image_b64),
            width=width,
            height=height,
            prompt=prompt.strip() or None,
            defry=defry or None,
        )

        async def _work() -> ImageResult:
            return await engine.run_director_tool(spec)

        return await self.run_in_background(
            _work,
            task_name=f"director_{self.tool_type}_{self.triggering_user_id}",
            purpose=f"action_director_{self.tool_type}",
            success_message=f"[内部：已发送{self.tool_display}结果]",
            error_prefix=f"{self.tool_display}失败",
        )


class DeclutterAction(BaseDirectorAction):
    """去杂物 — 清理图片中多余元素、遮挡物和文字。"""

    name: str = "director_declutter"
    description: str = (
        "对图片进行「去杂物」处理——清理图片中多余元素、遮挡物和文字。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n{FREE_TIER_HINT}"
    )
    tool_type: ClassVar[DirectorToolType] = "declutter"
    tool_display: ClassVar[str] = "去杂物"


class BgRemovalAction(BaseDirectorAction):
    """精细抠图 — 去背景，输出透明 PNG。"""

    name: str = "director_bg_removal"
    description: str = (
        "对图片进行「抠图」处理——精细去除背景，输出透明 PNG。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n"
        "⚠️ 始终消耗 65~200 Anlas（根据图片尺寸浮动），不享受免费额度。"
    )
    tool_type: ClassVar[DirectorToolType] = "bg-removal"
    tool_display: ClassVar[str] = "抠图"


class LineartAction(BaseDirectorAction):
    """提取线稿。"""

    name: str = "director_lineart"
    description: str = (
        "对图片进行「提取线稿」处理——从图片中提取线稿。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n{FREE_TIER_HINT}"
    )
    tool_type: ClassVar[DirectorToolType] = "lineart"
    tool_display: ClassVar[str] = "线稿"


class SketchAction(BaseDirectorAction):
    """转铅笔画 — 草图化。"""

    name: str = "director_sketch"
    description: str = (
        "对图片进行「铅笔画」处理——将图片转为铅笔画风格。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n{FREE_TIER_HINT}"
    )
    tool_type: ClassVar[DirectorToolType] = "sketch"
    tool_display: ClassVar[str] = "铅笔画"


class ColorizeAction(BaseDirectorAction):
    """线稿上色 — 需要 prompt 描述颜色方案。"""

    name: str = "director_colorize"
    description: str = (
        "对线稿图片进行「上色」处理——为线稿填充颜色。\n\n"
        "需要提供 prompt 参数描述颜色方案（如 'warm orange and blue tones'）。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n{FREE_TIER_HINT}"
    )
    tool_type: ClassVar[DirectorToolType] = "colorize"
    tool_display: ClassVar[str] = "上色"
    needs_prompt: ClassVar[bool] = True


class EmotionAction(BaseDirectorAction):
    """改变表情 — 需要 prompt 描述目标表情。"""

    name: str = "director_emotion"
    description: str = (
        "对图片中的人物进行「改变表情」处理。\n\n"
        "需要提供 prompt 参数描述目标表情（如 'happy'、'surprised'、'angry'）。\n\n"
        f"{IMAGE_SOURCE_HINT}\n\n{FREE_TIER_HINT}"
    )
    tool_type: ClassVar[DirectorToolType] = "emotion"
    tool_display: ClassVar[str] = "改表情"
    needs_prompt: ClassVar[bool] = True
