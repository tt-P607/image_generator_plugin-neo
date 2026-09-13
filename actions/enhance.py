"""NovelAI 普通与 Max Enhance Action。"""

from __future__ import annotations

from typing import Annotated, cast

from src.app.plugin_system.types import ChatType

from ..engine import EnhanceScale, EnhanceSpec, ImageResult
from .base import BaseImageAction


class EnhanceAction(BaseImageAction):
    """对已有图片执行普通 Enhance 或 V5 Max Enhance。"""

    name: str = "enhance_image"
    associated_types: list[str] = ["image"]
    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL
    description: str = (
        "增强已有图片细节。普通 Enhance 可选 1x、1.5x、2x，实际可用倍率由源图尺寸决定；"
        "Max 仅 V5 支持。它是 img2img 增强，与固定 2× upscale 不同。"
    )

    async def execute(
        self,
        prompt: Annotated[str, "原图对应的完整提示词。"],
        upscale: Annotated[str, "增强倍率：1x、1.5x、2x 或 Max。"] = "1.5x",
        strength: Annotated[float, "增强强度 0.01~0.99。"] = 0.5,
        noise: Annotated[float, "噪声 0.0~0.99；显式 0 会原样发送。"] = 0.0,
        model: Annotated[str, "可选模型；Max 必须使用 V5。"] = "",
        media_id: Annotated[str, "用户图片的媒体 ID。"] = "",
        image_filename: Annotated[str, "Bot 已生成图片的文件名。"] = "",
        output_filename: Annotated[str, "增强结果文件名，不含扩展名。"] = "",
    ) -> tuple[bool, str]:
        """执行 Enhance。"""

        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"
        if upscale not in ("1x", "1.5x", "2x", "Max"):
            return False, f"upscale 不合法（当前为 {upscale!r}）"
        source = await self.resolve_source_image(image_filename, media_id)
        if not source:
            return False, "需要提供 media_id 或 image_filename 才能增强图片"
        spec = EnhanceSpec(
            prompt=prompt,
            user_id=self.triggering_user_id,
            source_image=source,
            scale=cast(EnhanceScale, upscale),
            strength=strength,
            noise=noise,
            model=model.strip() or None,
        )

        async def _work() -> ImageResult:
            return await engine.enhance(spec)

        return await self.run_in_background(
            _work,
            task_name=f"enhance_action_{self.triggering_user_id}",
            purpose="action_enhance",
            success_message="[内部：已发送增强结果]",
            error_prefix="图片增强失败",
            output_filename=output_filename,
        )