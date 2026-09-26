"""NovelAI 固定 2× 放大 Action。"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.types import ChatType

from ..engine import ImageResult
from .base import BaseImageAction


class UpscaleAction(BaseImageAction):
    """通过 NovelAI 专用端点执行固定 2× 放大。"""

    name: str = "upscale_image"
    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL
    description: str = "将已有图片固定放大 2×；此功能与 img2img Enhance 相互独立。"

    async def execute(
        self,
        media_id: Annotated[str, "用户图片占位符或 Bot 出图 Action 返回的媒体 ID。"] = "",
    ) -> tuple[bool, str]:
        """执行固定 2× 放大。"""

        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"
        source = await self.resolve_source_image(media_id)
        if not source:
            return False, "需要提供 media_id 才能放大图片"

        async def _work() -> ImageResult:
            return await engine.upscale(source)

        return await self.run_in_background(
            _work,
            task_name=f"upscale_action_{self.triggering_user_id}",
            purpose="action_upscale",
            success_message="[内部：已发送固定 2× 放大结果]",
            error_prefix="图片放大失败",
        )