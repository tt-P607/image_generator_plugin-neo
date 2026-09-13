"""NovelAI 固定 2× 放大命令。"""

from __future__ import annotations

from src.app.plugin_system.base import cmd_route

from ..engine import ImageResult
from ..media import extract_image_from_stream_id
from . import replies
from .base import BaseImageCommand


class ImageUpscaleCommand(BaseImageCommand):
    """放大命令，需要引用一张图片。"""

    name: str = "nai_upscale"
    description: str = "NovelAI 固定 2× 图片放大"
    command_aliases: list[str] = ["放大图片", "图片放大"]

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """处理固定 2× 放大。"""

        engine = self.engine
        if engine is None:
            await self.reply("服务还没准备好呢，稍等一下")
            return False, "引擎未初始化"
        image_b64 = await extract_image_from_stream_id(self.stream_id, self._message)
        if not image_b64:
            await self.reply("请先引用一张需要放大的图片")
            return False, "未找到引用图片"

        async def _work() -> ImageResult:
            return await engine.upscale(image_b64, from_command=True)

        return await self.run_generation(
            _work,
            task_name=f"cmd_upscale_{self.user_scope}",
            purpose="command_upscale",
            success_hints=replies.EDIT_SUCCESS_HINTS,
            success_key="upscale_success",
        )