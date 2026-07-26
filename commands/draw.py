"""文生图、图生图与精密参考命令。

三者共享同一套画幅/提示词解析，出图统一走后台任务，命令立即返回。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import cmd_route

from ..engine import DirectorRefAsset, GenerationSpec, ImageResult
from ..media import extract_image_from_stream_id
from . import parsing, replies
from .base import BaseImageCommand

logger = get_logger("image_generator_plugin.command.draw")


class ImageGeneratorCommand(BaseImageCommand):
    """文生图命令。

    用法：
      /nai_image draw <提示词>              - 方图 (1024x1024)
      /nai_image draw 横图 <提示词>         - 横图 (1216x832)
      /nai_image draw 人物 <提示词>         - 套用人物预设
      /画图 <提示词> 负面: <负面词>          - 中文别名与负面词
      追加 --scale 7 --rescale 0.5 可覆盖引导参数
    """

    name: str = "nai_image"
    description: str = "NovelAI 文生图 - 根据提示词生成图片"
    command_aliases: list[str] = ["画图", "生图"]

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """处理省略子命令的文生图调用。"""

        return await self._draw(self.command_body())

    @cmd_route("draw")
    async def handle_draw(self) -> tuple[bool, str]:
        """处理 ``/nai_image draw`` 子路由。"""

        return await self._draw(self.command_body("draw"))

    async def _draw(self, raw_text: str) -> tuple[bool, str]:
        """执行文生图。

        Args:
            raw_text: 命令正文

        Returns:
            (是否已提交, 说明)
        """
        engine = self.engine
        if engine is None:
            await self.reply("服务还没准备好呢，稍等一下")
            return False, "引擎未初始化"

        flags = parsing.extract_scale_flags(raw_text)
        tokens = flags.remainder.split()
        if not tokens:
            await self.reply(replies.pick(replies.MISSING_PROMPT_HINTS, "missing_prompt"))
            return False, "缺少提示词参数"

        size = parsing.DEFAULT_SIZE
        preset = parsing.DRAW_PRESETS.get(tokens[0].lower())
        consumed = 0

        if preset is not None:
            size = preset.size
            consumed = 1
        else:
            parsed_size = parsing.parse_size_token(tokens[0])
            if parsed_size is not None:
                if parsed_size not in parsing.SUPPORTED_SIZES:
                    await self.reply(
                        replies.pick(
                            replies.UNSUPPORTED_SIZE_HINTS, "unsupported_size"
                        ).format(size=f"{parsed_size[0]}x{parsed_size[1]}")
                    )
                    return False, "画幅不支持"
                size = parsed_size
                consumed = 1

        raw_prompt = " ".join(tokens[consumed:])
        if not raw_prompt.strip():
            await self.reply(replies.pick(replies.SIZE_NO_PROMPT_HINTS, "size_no_prompt"))
            return False, "缺少提示词"

        prompt, negative_prompt = parsing.split_prompt(raw_prompt)
        if preset is not None:
            prompt = preset.apply(prompt)

        label = parsing.SIZE_LABELS[size]
        await self.reply(
            replies.pick(replies.START_DRAWING_HINTS[label], f"start_{label}")
        )

        spec = GenerationSpec(
            prompt=prompt,
            user_id=self.user_scope,
            negative_prompt=negative_prompt,
            width=size[0],
            height=size[1],
            scale=flags.scale,
            cfg_rescale=flags.cfg_rescale,
            from_command=True,
        )

        async def _work() -> ImageResult:
            return await engine.generate(spec)

        return await self.run_generation(
            _work,
            task_name=f"cmd_draw_{self.user_scope}",
            purpose="command_draw",
            success_hints=replies.DRAW_SUCCESS_HINTS,
            success_key="draw_success",
        )


class ImageEditCommand(BaseImageCommand):
    """图生图命令。

    用法：
      /nai_edit edit <提示词> [强度 0.1-0.99]
      /改图 <提示词> [强度]
    需要引用一张图片。
    """

    name: str = "nai_edit"
    description: str = "NovelAI 图生图 - 基于引用图片进行编辑"
    command_aliases: list[str] = ["改图", "修图"]

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """处理省略子命令的图生图调用。"""

        return await self._edit(self.command_body())

    @cmd_route("edit")
    async def handle_edit(self) -> tuple[bool, str]:
        """处理 ``/nai_edit edit`` 子路由。"""

        return await self._edit(self.command_body("edit"))

    async def _edit(self, raw_text: str) -> tuple[bool, str]:
        """执行图生图。

        Args:
            raw_text: 命令正文

        Returns:
            (是否已提交, 说明)
        """
        engine = self.engine
        if engine is None:
            await self.reply("服务还没准备好呢，稍等一下")
            return False, "引擎未初始化"

        tokens = raw_text.split()
        if not tokens:
            await self.reply("想改图的话，先引用一张图片然后告诉我怎么改")
            return False, "缺少参数"

        image_b64 = await extract_image_from_stream_id(self.stream_id, self._message)
        if not image_b64:
            await self.reply("我需要一张图片才能帮你修改呀，记得引用图片哦")
            return False, "未找到引用图片"

        prompt, strength = parsing.parse_edit_args(tokens)
        strength_text = f"，修改强度 {strength}" if strength else ""
        await self.reply(
            replies.pick(replies.START_EDITING_HINTS, "start_edit").format(
                strength=strength_text
            )
        )

        spec = GenerationSpec(
            prompt=prompt,
            user_id=self.user_scope,
            source_image=image_b64,
            strength=strength,
            from_command=True,
        )

        async def _work() -> ImageResult:
            return await engine.generate(spec)

        return await self.run_generation(
            _work,
            task_name=f"cmd_edit_{self.user_scope}",
            purpose="command_edit",
            success_hints=replies.EDIT_SUCCESS_HINTS,
            success_key="edit_success",
        )


class ImageReferenceCommand(BaseImageCommand):
    """精密参考命令。

    引用一张图片再发本命令，会把该图片作为 NovelAI 精密参考
    （Director Reference）参与文生图——不直接重绘，而是把图片的
    人物/风格特征与提示词融合。

    用法：
      /nai_ref [ref] <提示词> [--type 角色|风格|两者]
                              [--fidelity 0-1] [--strength 0-1]
      /参考图 <提示词> ...
    """

    name: str = "nai_ref"
    description: str = "精确参考图生图（引用图片+提示词）"
    command_aliases: list[str] = ["参考图"]

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """处理省略子命令的精密参考调用。"""

        return await self._reference(self.command_body())

    @cmd_route("ref")
    async def handle_ref(self) -> tuple[bool, str]:
        """处理 ``/nai_ref ref`` 子路由。"""

        return await self._reference(self.command_body("ref"))

    async def _reference(self, raw_text: str) -> tuple[bool, str]:
        """执行精密参考生图。

        Args:
            raw_text: 命令正文

        Returns:
            (是否已提交, 说明)
        """
        engine = self.engine
        if engine is None:
            await self.reply("服务还没准备好呢，稍等一下")
            return False, "引擎未初始化"

        if not raw_text.strip():
            await self.reply("引用一张图片，再告诉我要画什么，我会参考那张图的风格来生成")
            return False, "缺少参数"

        image_b64 = await extract_image_from_stream_id(self.stream_id, self._message)
        if not image_b64:
            await self.reply("需要引用一张图片才能精确参考哦，回复一张图片再发命令试试")
            return False, "未找到引用图片"

        reference_flags = parsing.extract_reference_flags(raw_text)
        scale_flags = parsing.extract_scale_flags(reference_flags.remainder)
        prompt, negative_prompt = parsing.split_prompt(scale_flags.remainder)
        if not prompt:
            await self.reply("提示词不能为空哦~")
            return False, "提示词为空"

        type_label = {
            "character": "角色",
            "style": "风格",
            "character&style": "两者",
        }[reference_flags.ref_type]
        await self.reply(
            f"好的，参考那张图来生成（类型={type_label}, "
            f"忠实度={reference_flags.fidelity:.1f}, "
            f"强度={reference_flags.strength:.1f}）"
        )

        spec = GenerationSpec(
            prompt=prompt,
            user_id=self.user_scope,
            negative_prompt=negative_prompt,
            scale=scale_flags.scale,
            cfg_rescale=scale_flags.cfg_rescale,
            director_refs=(
                DirectorRefAsset(
                    data=image_b64,
                    ref_type=reference_flags.ref_type,  # type: ignore[arg-type]
                    fidelity=reference_flags.fidelity,
                    strength=reference_flags.strength,
                ),
            ),
            from_command=True,
        )

        async def _work() -> ImageResult:
            return await engine.generate(spec)

        return await self.run_generation(
            _work,
            task_name=f"cmd_ref_{self.user_scope}",
            purpose="command_reference",
            success_hints=replies.EDIT_SUCCESS_HINTS,
            success_key="edit_success",
        )
