"""AI 画图 Action。

把模型给出的自然语言描述转换为 NovelAI 标签式提示词并出图。
可用画风、预设和参考图列表由插件在加载时注入到 description。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from ..descriptions import BASE_DRAW_DESCRIPTION
from ..engine import CharacterPrompt, GenerationSpec, ImageResult
from .base import BaseImageAction

logger = get_logger("image_generator_plugin.draw_action")

NO_STYLE_FLAG = "no_style"
DEFAULT_RESOLUTION = "832x1216"


class DrawAction(BaseImageAction):
    """AI 画图动作 — 将描述转换为 NovelAI 提示词并生成图片。"""

    name: str = "draw_image"
    description: str = BASE_DRAW_DESCRIPTION
    associated_types: list[str] = ["image"]
    primary_action: bool = True
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        content_description: Annotated[
            str,
            "图片内容的英文 NovelAI 标签，禁止中文。"
            "权重语法：n::tag::（提升）/ n::tag::（降低）。"
            "角色格式：character_name (source)；皮肤/来源精确拼写。",
        ],
        output_filename: Annotated[
            str,
            "必填。输出文件名（不含扩展名，仅英文/数字/下划线）。"
            "图片以此文件名保存，后续 inpaint_image / director_tool 可通过此文件名引用。"
            "例如：'character_portrait_01' 或 'landscape_sunset_02'。",
        ],
        resolution: Annotated[
            str,
            "图片画幅尺寸。横图用 '1216x832'，竖图用 '832x1216'，方图用 '1024x1024'。"
            "根据内容主体形态选择。",
        ] = DEFAULT_RESOLUTION,
        negative_prompt: Annotated[
            str,
            "场景专属额外排除词，英文逗号分隔。此处只填本次图片特有的排除内容。"
            "系统已内置的通用负面词无需重复填写。",
        ] = "",
        selected_vibes: Annotated[
            str,
            "从可用画风列表中选择要应用的 Vibe 名称，多个用英文逗号分隔。"
            "不需要风格或无可选列表时留空。",
        ] = "",
        selected_director_refs: Annotated[
            str,
            "从可用精密参考列表中选择要应用的参考图名称，多个用英文逗号分隔。"
            "不需要参考或无可用列表时留空。",
        ] = "",
        characters: Annotated[
            str,
            "多人物 JSON 数组字符串（仅 V4 系列模型）。最多 6 个角色，每项 "
            "{prompt, uc?, x?, y?}；x/y 为 0~1 浮点坐标。"
            "互动用 source#/target#/mutual# 语法。单人物时留空。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行画图动作。"""
        if not content_description.strip():
            logger.warning("画图动作未提供内容描述")
            return False, "画什么呢？请告诉我你想要的图片内容~"

        engine = self.engine
        if engine is None:
            return False, "图片生成服务不可用"

        parsed_characters = _parse_characters(characters)
        if isinstance(parsed_characters, str):
            return False, parsed_characters

        width, height = self.parse_resolution(resolution, DEFAULT_RESOLUTION)
        prompt = self._build_prompt(content_description)
        logger.info(f"AI 画图 - 提示词: {prompt}, 画幅: {width}x{height}")

        spec = GenerationSpec(
            prompt=prompt,
            user_id=self.triggering_user_id,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            selected_vibe_names=_split_names(selected_vibes),
            director_refs=engine.assets.select_director_refs(
                _split_names(selected_director_refs)
            ),
            characters=parsed_characters,
        )

        async def _work() -> ImageResult:
            return await engine.generate(spec)

        return await self.run_in_background(
            _work,
            task_name=f"draw_action_{self.triggering_user_id}",
            purpose="action_draw",
            success_message="[内部：已发送画作]",
            error_prefix="画画失败了",
            output_filename=output_filename,
        )

    def _build_prompt(self, content_tags: str) -> str:
        """拼接画风标签、匹配到的预设与模型给出的内容标签。

        content_tags 中包含 ``no_style`` 且配置允许时跳过画风注入。

        Args:
            content_tags: 模型给出的英文标签

        Returns:
            完整提示词
        """
        config = self.plugin_config
        allow_skip = config.generation.allow_skip_style
        skip_style = allow_skip and NO_STYLE_FLAG in content_tags
        if skip_style:
            logger.info("模型请求跳过画风标签注入（no_style）")

        clean_tags = content_tags.replace(NO_STYLE_FLAG, "").strip().strip(",").strip()

        style = "" if skip_style else config.generation.style_reference.strip()
        presets = [
            preset.content.strip()
            for preset in config.prompt.presets
            if preset.content.strip()
            and (
                preset.name in clean_tags
                or (preset.trigger and preset.trigger in clean_tags)
            )
        ]

        parts = [part for part in [style, *presets, clean_tags] if part]
        return ", ".join(parts)


def _split_names(raw: str) -> tuple[str, ...]:
    """把逗号分隔的名称串拆成元组。

    Args:
        raw: 逗号分隔的名称文本

    Returns:
        去空后的名称元组
    """
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _parse_characters(raw: str | list[Any]) -> tuple[CharacterPrompt, ...] | str:
    """解析多人物参数。

    模型可能直接给出 JSON 数组对象而非字符串，此处统一处理。

    Args:
        raw: JSON 数组字符串或已解析的列表

    Returns:
        角色元组，或描述问题的错误文本
    """
    if isinstance(raw, list):
        data: Any = raw
    else:
        if not raw.strip():
            return ()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            return f"characters 不是合法 JSON：{error}"

    if not isinstance(data, list):
        return "characters 必须是 JSON 数组"
    if not data:
        return ()

    characters: list[CharacterPrompt] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            return f"characters[{index}] 不是对象"

        prompt = str(item.get("prompt", "")).strip()
        if not prompt:
            return f"characters[{index}].prompt 不能为空"

        try:
            x = float(item.get("x", 0.5))
            y = float(item.get("y", 0.5))
        except (TypeError, ValueError):
            return f"characters[{index}] 的 x/y 必须是数字"
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return f"characters[{index}] 的 x/y 必须在 0.0~1.0 区间"

        characters.append(
            CharacterPrompt(
                prompt=prompt,
                negative_prompt=str(item.get("uc", "") or "").strip(),
                x=x,
                y=y,
            )
        )
    return tuple(characters)
