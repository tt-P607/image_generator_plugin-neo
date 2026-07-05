"""AI 画图 Action — 执行实际的图片生成。

此 Action 携带完整的 NovelAI 提示词语法说明、
预设信息、可选 Vibe 列表等详细参数。模型将自然语言描述
转换为精确的 NovelAI 标签式提示词。

设计意图：
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

    内置会话级图片记忆：每次出图后自动保存该聊天流的上次提示词，
    下次激活时注入到 action_description，供模型保持服装与场景一致性。
    """

    # 会话级图片记忆：stream_id -> 上次出图的 content_description
    _image_memory: dict[str, str] = {}

    # 记忆块标记，用于定位和替换
    _MEMORY_MARKER: str = "\n\n【上次出图 tags 参考】"

    action_name: str = "draw_image"
    associated_types: list[str] = ["image"]
    action_description: str = (
        "**1. 基础结构**\n"
        "提示词必须为英文、半角逗号分隔的标签式结构。越靠前权重越高。\n"
        "推荐顺序：风格 → 人数/性别 → 角色身份 → 身体特征 → "
        "服装配饰 → 动作表情 → 环境背景 → 光影视角\n\n"
        "**2. 权重语法（NovelAI 冒号语法）**\n"
        "  提升：n::Tag::（推荐 n=1.1~1.4）\n"
        "  降低：n::Tag::（推荐 n=0.6~0.9）\n"
        "  旧版：{Tag} ≈x1.1，[Tag] ≈x0.9，建议弃用\n"
        "  注意：以数字结尾的词条末尾需加空格或下划线，否则权重会影响后续词条\n\n"
        "**3. 角色与皮肤标签**\n"
        "  角色：角色名 (作品名)，如 Castorice (honkai: star rail)\n"
        "  皮肤：角色名_(皮肤名)_(作品名)，如 Hu_Tao_(Cherries_Snow-Laden)_(genshin_impact)\n"
        "  游戏 CG 风格：1.2::game_name (game cg)::\n"
        "  大小写不敏感，特殊符号需还原\n\n"
        "**4. 画面文字**\n"
        "  语法：TEXT: 要显示的文字（TEXT 大写）\n"
        "  可多次使用，配合 speech bubble 实现漫画效果\n\n"
        "**5. 多角色与位置控制**\n"
        "使用 characters 参数传入 JSON 数组，最多 6 个角色，每项字段：\n"
        "  - prompt：该角色英文标签（必填）\n"
        "  - uc：角色专属负面词（可省略）\n"
        "  - x：水平 0.0~1.0（0=最左，1=最右，默认 0.5）\n"
        "  - y：垂直 0.0~1.0（0=最上，1=最下，默认 0.5）\n"
        "示例：[{\"prompt\":\"1girl, blonde hair\",\"x\":0.3,\"y\":0.5},"
        "{\"prompt\":\"1girl, black hair\",\"x\":0.7,\"y\":0.5}]\n\n"
        "**6. 互动标签**\n"
        "  施动方：source#动作（如 source#hugging）\n"
        "  受动方：target#动作（如 target#hugging）\n"
        "  相互：mutual#动作（如 mutual#holding hands）\n"
        "  规则：source/target 必须成对出现在不同角色上；"
        "不要在 content_description 重复互动动作；"
        "同一对不能同时用 source# 和 mutual#\n\n"
        "**7. 精确参考 (Director Reference)**\n"
        "使用 selected_director_refs 参数，填入要使用的参考图名称（逗号分隔）。\n"
        "可用名称由系统在上下文中提供，不在列表中的名称无效。\n\n"
        "**注意事项**\n"
        "  - 多角色时 content_description 只写环境/光影/构图/人数，角色细节放 characters\n"
        "  - 单人物不要传 characters，留空即可\n"
        "  - 仅 V4 系列模型支持多人物和精确参考\n\n"
        "**画幅**：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024"
    )

    primary_action: bool = True
    chat_type: ChatType = ChatType.ALL

    async def go_activate(self) -> bool:
        """激活时注入上次出图记忆到 action_description（按聊天流隔离）。"""
        stream_id = getattr(self.chat_stream, "stream_id", "")
        if not stream_id:
            return True

        last_tags = self._image_memory.get(stream_id)
        if last_tags:
            # 移除旧的记忆块（如果存在），再追加新的
            base_desc = DrawAction.action_description
            if self._MEMORY_MARKER in base_desc:
                base_desc = base_desc.split(self._MEMORY_MARKER)[0].rstrip()
            memory_block = (
                f"{self._MEMORY_MARKER}\n{last_tags}\n"
                "⚠️ 如果当前聊天流在连续叙事中，请尽可能保持场景和服装的一致性，"
                "使用与上次相同的服装和场景 tags。如需换装或切换场景，"
                "请在 content_description 中写明新的标签。"
            )
            type(self).action_description = base_desc + memory_block
            logger.debug(f"已注入上次出图记忆 (stream={stream_id[:8]})")

        return True

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
        selected_director_refs: Annotated[
            str,
            "从可用精密参考列表中选择要应用的参考图名称，多个用英文逗号分隔。"
            "不需要参考或无可用列表时留空。",
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
        # 容错：LLM 可能直接返回 JSON 数组对象而非字符串，此处统一规范化为字符串。
        # 空数组视为无多人物，转成空字符串以跳过后续解析。
        if isinstance(characters, list):
            characters = json.dumps(characters) if characters else ""
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

        # 解析自选精密参考（从预设池按名称查找真实图片数据）
        ref_images: list[dict[str, Any]] = []
        if selected_director_refs.strip():
            service = self.get_service()
            if service and hasattr(service, "selectable_director_refs"):
                names = [n.strip() for n in selected_director_refs.split(",") if n.strip()]
                for name in names:
                    ref = service.selectable_director_refs.get(name)
                    if ref:
                        ref_images.append(ref)
                        logger.info(f"LLM 选择了精密参考: {name}")
                    else:
                        logger.warning(f"LLM 选择了不存在的精密参考: {name}")

        final_refs = ref_images

        result = await self.generate_and_send_image(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            width=width,
            height=height,
            success_message="[内部：已发送画作]",
            error_prefix="画画失败了",
            selected_vibe_names=vibe_names,
            character_prompts=character_prompts,
            reference_images=final_refs or None,
        )

        # 出图成功后保存 content_description 到会话级记忆
        if result[0]:
            stream_id = getattr(self.chat_stream, "stream_id", "")
            if stream_id:
                self._image_memory[stream_id] = content_description
                logger.debug(f"已保存上次出图 tags 到会话记忆 (stream={stream_id[:8]})")

        return result

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

        从插件配置读取 style_reference，默认拼接到提示词最前面。
        如果 content_tags 中包含 `no_style`，则跳过画风标签注入。
        同时检查是否有匹配的预设，并注入预设内容。

        Args:
            content_tags: AI 已转换为英文标签的内容描述

        Returns:
            完整的提示词字符串
        """
        # 检查是否跳过画风注入（受 allow_skip_style 开关控制）
        allow_skip = True
        config = getattr(self.plugin, "config", None)
        if config is not None:
            gen = getattr(config, "generation", None)
            if gen is not None:
                allow_skip = getattr(gen, "allow_skip_style", True)

        skip_style = allow_skip and "no_style" in content_tags
        # 清理掉 no_style 标志本身，不传给 API
        clean_tags = content_tags.replace("no_style", "").strip().strip(",").strip()

        style_ref = ""
        preset_content = ""

        config = getattr(self.plugin, "config", None)
        if config is not None:
            # 1. 获取全局画风参考（可被 no_style 跳过）
            if not skip_style:
                gen = getattr(config, "generation", None)
                if gen is not None:
                    style_ref = getattr(gen, "style_reference", "") or ""

            # 2. 匹配预设，注入预设内容
            prompt_cfg = getattr(config, "prompt", None)
            if prompt_cfg and hasattr(prompt_cfg, "presets"):
                for preset in prompt_cfg.presets:
                    if preset.name in clean_tags or (preset.trigger and preset.trigger in clean_tags):
                        if preset.content.strip():
                            preset_content += "\n" + preset.content

        if skip_style:
            logger.info("LLM 请求跳过画风标签注入（no_style）")

        parts = [p for p in [style_ref.strip(), preset_content.strip(), clean_tags.strip()] if p]
        return ", ".join(parts)
