"""AI 画图 Action — 执行实际的图片生成。

此 Action 在 Tool 被调用后激活，携带完整的 NovelAI 提示词语法说明、
预设信息、可选 Vibe 列表等详细参数。模型在此阶段将自然语言描述
转换为精确的 NovelAI 标签式提示词。

设计意图：
- 普通对话中模型只看到轻量的 Tool（参数少、schema 小）
- 需要画图时 Tool 被调用，随后 Action 被激活
- Action 的 description 包含完整的提示词编写指南和预设
- 用户可配置的预设、角色标签、Vibe 列表等都在此处注入
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from .base_image_action import BaseImageAction

logger = get_logger("image_generator_plugin.draw_action")


class DrawAction(BaseImageAction):
    """AI 画图动作 — 将描述转换为 NovelAI 提示词并生成图片。

    此 Action 不直接暴露给模型日常对话，而是在 draw_image Tool
    被调用后由系统激活。激活时模型会看到完整的提示词编写指南。
    """

    action_name: str = "draw_image"
    action_description: str = (
        "执行图片生成：将画面描述转换为 NovelAI 标签式提示词并生图。\n\n"
        "=== NovelAI 提示词高级用法总结 ===\n\n"
        "本指南指导你将用户的自然语言请求，转化为 NovelAI 模型能高效率、"
        "高精度执行的结构化、加权标签提示词。\n\n"
        "**1. 基础结构与顺序（核心原则）**\n"
        "提示词必须以英文、半角符号、逗号分隔的词条式 (Tag-based) 结构呈现。\n"
        "推荐顺序（越靠前权重越高）：\n"
        "  1. 艺术家与风格\n"
        "  2. 角色数量与性别\n"
        "  3. 角色身份与性质\n"
        "  4. 身体特征\n"
        "  5. 服装与配饰\n"
        "  6. 动作与表情\n"
        "  7. 环境与背景\n"
        "  8. 光影与视角\n"
        "  9. 整体风格\n\n"
        "主提示词示例：masterpiece, best quality, ultra detailed, official art, "
        "1girl, ((red eyes)), (silver hair:1.3), maid dress, holding sword, "
        "outdoors, night sky, volumetric lighting\n\n"
        "**2. 高级权重语法（NovelAI 特有）**\n"
        "必须使用冒号权重语法来精确控制焦点，而非旧版括号。\n"
        "  提升权重：n::Tag::，将标签权重提升 n 倍，推荐 n 取 1.1~1.4\n"
        "  降低权重：n::Tag::，将标签权重降低 n 倍，推荐 n 取 0.6~0.9\n"
        "  旧版语法：{Tag} 提升约 x1.1；[Tag] 降低约 x0.9。建议弃用，使用冒号语法。\n"
        "  注意：以数字结尾的词条末尾需加空格/下划线/斜杠，"
        "否则权重可能影响到之后的所有词条。\n\n"
        "**3. 特殊角色与皮肤的指定（精确识别）**\n"
        "当涉及有来源的角色、皮肤或作品名时，必须使用 Danbooru 风格标签精确限定。\n"
        "  指定作品/角色：角色名 (作品名)，如 Castorice (honkai: star rail)\n"
        "  指定皮肤/变体：角色名_(皮肤名)_(作品名)，"
        "如 Hu_Tao_(Cherries_Snow-Laden)_(genshin_impact)\n"
        "  游戏/CG 风格：加权游戏名标签，如 1.2::honkai: star rail (game cg)::\n"
        "  通用规则：提示词大小写不敏感；带特殊符号的名称输入时需还原。\n\n"
        "**4. 画面中的文字生成（精准置入）**\n"
        "NovelAI 支持在图像中嵌入特定文字。\n"
        "  语法：TEXT: 想要显示的文字（TEXT 需大写）\n"
        "  多处文字可多次使用 TEXT: 标签\n"
        "  可配合 speech bubble 等标签实现漫画效果\n\n"
        "**5. 多角色提示词与位置控制（多角色控制）**\n"
        "使用 characters 参数为每个角色设置独立提示词框。\n"
        "  传入 JSON 数组，最多 6 个角色，每项字段：\n"
        "    - prompt：该角色的英文标签（必填）\n"
        "    - uc：该角色专属负面词，可省略（默认空）\n"
        "    - x：水平位置 0.0~1.0，0=最左，1=最右；可省略（默认 0.5）\n"
        "    - y：垂直位置 0.0~1.0，0=最上，1=最下；可省略（默认 0.5）\n"
        "  示例：[{\"prompt\":\"1girl, blonde hair\",\"x\":0.3,\"y\":0.5},"
        "{\"prompt\":\"1girl, black hair\",\"x\":0.7,\"y\":0.5}]\n"
        "  建议明确划定角色位置以避免左右混淆。\n\n"
        "**6. 互动标签语法（精确控制）**\n"
        "在角色专属提示词中使用互动指令配合动作词条。\n"
        "  施动方：source#动作，代表发起动作的角色，如 source#hugging\n"
        "  受动方：target#动作，代表接受动作的角色，如 target#hugging\n"
        "  相互互动：mutual#动作，双方对等无主次，如 mutual#holding hands\n"
        "  规则：\n"
        "  - source# 和 target# 必须成对出现在不同角色身上\n"
        "  - 不要在全局 content_description 里重复写互动动作\n"
        "  - 不能同一对角色既写 source#xxx 又写 mutual#xxx（会冲突）\n\n"
        "**使用注意**\n"
        "  - characters 不为空时，content_description 聚焦画面环境/光影/构图/数量统计"
        "（如 \"2girls\"），单角色细节交给 characters\n"
        "  - 仅 V4 系列模型支持多人物，V3 调用会报错\n"
        "  - 单人物图片不要传 characters，留空即可\n\n"
        "**画幅选择**：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024。"
    )

    primary_action: bool = False
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        content_description: Annotated[
            str,
            "图片内容的英文 NovelAI 标签，禁止中文。"
            "权重语法：n::tag::（提升）/ n::tag::（降低）。"
            "角色格式：character_name (source)；皮肤/来源精确拼写。",
        ],
        resolution: Annotated[
            str,
            "图片画幅尺寸。横图用 '1216x832'，竖图用 '832x1216'，方图用 '1024x1024'。"
            "根据内容主体形态选择。",
        ] = "832x1216",
        negative_prompt: Annotated[
            str,
            "场景专属额外排除词，英文逗号分隔。此处只填本次图片特有的排除内容。",
        ] = "",
        selected_vibes: Annotated[
            str,
            "从可用画风列表中选择要应用的 Vibe 名称，多个用英文逗号分隔。"
            "不需要风格或无可选列表时留空。",
        ] = "",
        characters: Annotated[
            str,
            "多人物 JSON 数组字符串（仅 V4 系列模型）。最多 6 个角色，每项 "
            "{prompt, uc?, x?, y?}；x/y 为 0~1 浮点坐标。互动用 source#/target#/mutual# 语法。"
            "单人物时留空。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行画图动作。"""
        if not content_description:
            logger.warning("画图动作未提供内容描述")
            return False, "画什么呢？请告诉我你想要的图片内容~"

        width, height = self._parse_resolution(resolution, default="832x1216")

        prompt = self._build_prompt(content_description)
        logger.info(f"AI 画图 - 提示词: {prompt}, 画幅: {width}x{height}")

        if negative_prompt:
            logger.info(f"用户负面提示词: {negative_prompt}")

        # 解析逗号分隔的 Vibe 名称
        vibe_names: list[str] | None = None
        if selected_vibes.strip():
            vibe_names = [v.strip() for v in selected_vibes.split(",") if v.strip()]
            logger.info(f"LLM 选择的 Vibe: {vibe_names}")

        # 解析多人物 JSON
        character_prompts: list[dict[str, Any]] | None = None
        if characters.strip():
            parsed_or_msg = self._parse_characters(characters)
            if isinstance(parsed_or_msg, str):
                return False, parsed_or_msg
            character_prompts = parsed_or_msg
            logger.info(
                f"多人物模式：{len(character_prompts)} 个角色 "
                f"-> {[(c['prompt'][:30], c['x'], c['y']) for c in character_prompts]}"
            )

        return await self.generate_and_send_image(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            success_message="[内部：已发送画作]",
            error_prefix="画画失败了",
            selected_vibe_names=vibe_names,
            character_prompts=character_prompts,
        )

    def _parse_characters(
        self, raw: str
    ) -> list[dict[str, Any]] | str:
        """解析 LLM 传入的 characters JSON 字符串。

        Args:
            raw: JSON 数组字符串

        Returns:
            解析后的角色列表，或错误描述字符串
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"characters 不是合法 JSON：{e!s}"

        if not isinstance(data, list):
            return "characters 必须是 JSON 数组"
        if not data:
            return "characters 数组不能为空（单人物请留空该参数）"

        result: list[dict[str, Any]] = []
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                return f"characters[{idx}] 不是对象"
            char_prompt = str(item.get("prompt", "")).strip()
            if not char_prompt:
                return f"characters[{idx}].prompt 不能为空"
            try:
                x = float(item.get("x", 0.5))
                y = float(item.get("y", 0.5))
            except (TypeError, ValueError):
                return f"characters[{idx}] 的 x/y 必须是数字"
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                return f"characters[{idx}] 的 x/y 必须在 0.0~1.0 区间"
            result.append({
                "prompt": char_prompt,
                "uc": str(item.get("uc", "") or "").strip(),
                "x": x,
                "y": y,
            })
        return result

    def _build_prompt(self, content_tags: str) -> str:
        """构建图片生成提示词。

        从插件配置读取 quality_prefix 和 quality_suffix，
        拼接为：prefix + content_tags + suffix。

        Args:
            content_tags: AI 已转换为英文标签的内容描述

        Returns:
            完整的提示词字符串
        """
        # 从插件配置读取前后缀，回退到默认值
        prefix = "masterpiece, best quality, ultra detailed, official art, 1.3::very aesthetic::"
        suffix = "beautiful lighting"

        config = getattr(self.plugin, "config", None)
        if config is not None:
            gen = getattr(config, "generation", None)
            if gen is not None:
                prefix = getattr(gen, "quality_prefix", prefix) or prefix
                suffix = getattr(gen, "quality_suffix", suffix) or suffix

        parts = [p for p in [prefix.strip(), content_tags.strip(), suffix.strip()] if p]
        return ", ".join(parts)
