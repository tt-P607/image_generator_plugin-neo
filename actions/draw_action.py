"""AI 画图 Action — 执行实际的图片生成。

此 Action 携带完整的 NovelAI 提示词语法说明、
预设信息、可选 Vibe 列表等详细参数。模型将自然语言描述
转换为精确的 NovelAI 标签式提示词。

设计意图：
- Action 的 description 包含完整的提示词编写指南和预设
- 用户可配置的预设、角色标签、Vibe 列表等都在此处注入
- 支持单图和批量多图生成（通过 batch_prompts 参数）
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatType

from .base_image_action import BaseImageAction

logger = get_logger("image_generator_plugin.draw_action")


class DrawAction(BaseImageAction):
    """AI 画图动作 — 将描述转换为 NovelAI 提示词并生成图片。"""

    action_name: str = "draw_image"
    action_description: str = (
        "执行图片生成：将画面描述转换为 NovelAI 标签式提示词并生图。\n"
        "支持单图和批量多图生成。批量时使用 batch_prompts 参数传入 JSON 数组，按顺序逐张生成。\n\n"
        "=== NovelAI 提示词语法指南 ===\n\n"
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
        "**7. 批量生成多张图**\n"
        "使用 batch_prompts 参数传入 JSON 数组，每项为一张图的配置对象：\n"
        "  - prompt：该图的英文标签提示词（必填）\n"
        "  - resolution：画幅，可选覆盖（如 '1216x832'）\n"
        "  - negative_prompt：该图专属负面词，可选覆盖\n"
        "  - selected_vibes：该图的 Vibe 选择，可选覆盖\n"
        "  - characters：该图的多人物 JSON 数组，可选覆盖\n"
        "未指定的字段使用外层参数作为默认值。\n"
        "示例：[{\"prompt\":\"1girl, sunset, beach\"},{\"prompt\":\"1girl, night, city\","
        "\"resolution\":\"1216x832\"}]\n"
        "注意：使用 batch_prompts 时，content_description 将被忽略。\n\n"
        "**注意事项**\n"
        "  - 多角色时 content_description 只写环境/光影/构图/人数，角色细节放 characters\n"
        "  - 单人物不要传 characters，留空即可\n"
        "  - 仅 V4 系列模型支持多人物\n\n"
        "**画幅**：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024"
    )

    primary_action: bool = True
    chat_type: ChatType = ChatType.ALL

    async def execute(
        self,
        content_description: Annotated[
            str,
            "图片内容的英文 NovelAI 标签，禁止中文。"
            "权重语法：n::tag::（提升）/ n::tag::（降低）。"
            "角色格式：character_name (source)；皮肤/来源精确拼写。"
            "批量生成时此参数被忽略，使用 batch_prompts 代替。",
        ] = "",
        resolution: Annotated[
            str,
            "图片画幅尺寸。横图用 '1216x832'，竖图用 '832x1216'，方图用 '1024x1024'。"
            "根据内容主体形态选择。批量模式下作为默认画幅。",
        ] = "832x1216",
        negative_prompt: Annotated[
            str,
            "场景专属额外排除词，英文逗号分隔。此处只填本次图片特有的排除内容。"
            "批量模式下作为默认负面提示词。",
        ] = "",
        selected_vibes: Annotated[
            str,
            "从可用画风列表中选择要应用的 Vibe 名称，多个用英文逗号分隔。"
            "不需要风格或无可选列表时留空。批量模式下作为默认 Vibe。",
        ] = "",
        characters: Annotated[
            str,
            "多人物 JSON 数组字符串（仅 V4 系列模型）。最多 6 个角色，每项 "
            "{prompt, uc?, x?, y?}；x/y 为 0~1 浮点坐标。互动用 source#/target#/mutual# 语法。"
            "单人物时留空。批量模式下作为默认多人物配置。",
        ] = "",
        batch_prompts: Annotated[
            str,
            "批量生成多张图的 JSON 数组字符串。每项为对象，prompt 必填，"
            "resolution/negative_prompt/selected_vibes/characters 可选覆盖外层默认值。"
            "示例：[{\"prompt\":\"1girl, sunset\"},{\"prompt\":\"1girl, night\",\"resolution\":\"1216x832\"}]"
            "单图时留空，使用 content_description 即可。",
        ] = "",
    ) -> tuple[bool, str]:
        """执行画图动作，支持单图和批量多图生成。"""
        # 批量模式：解析 batch_prompts 并逐张生成
        if batch_prompts.strip():
            return await self._execute_batch(
                batch_prompts=batch_prompts,
                default_resolution=resolution,
                default_negative_prompt=negative_prompt,
                default_selected_vibes=selected_vibes,
                default_characters=characters,
            )

        # 单图模式（向后兼容）
        if not content_description:
            logger.warning("画图动作未提供内容描述")
            return False, "画什么呢？请告诉我你想要的图片内容~"

        return await self._execute_single(
            content_description=content_description,
            resolution=resolution,
            negative_prompt=negative_prompt,
            selected_vibes=selected_vibes,
            characters=characters,
        )

    async def _execute_single(
        self,
        content_description: str,
        resolution: str,
        negative_prompt: str,
        selected_vibes: str,
        characters: str,
    ) -> tuple[bool, str]:
        """执行单张图片生成。

        Args:
            content_description: 图片内容标签
            resolution: 画幅尺寸
            negative_prompt: 负面提示词
            selected_vibes: Vibe 名称（逗号分隔）
            characters: 多人物 JSON 字符串

        Returns:
            (是否成功, 消息)
        """
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

    async def _execute_batch(
        self,
        batch_prompts: str,
        default_resolution: str,
        default_negative_prompt: str,
        default_selected_vibes: str,
        default_characters: str,
    ) -> tuple[bool, str]:
        """批量生成多张图片，按顺序逐张生成并发送。

        Args:
            batch_prompts: JSON 数组字符串，每项包含 prompt 及可选覆盖参数
            default_resolution: 默认画幅
            default_negative_prompt: 默认负面提示词
            default_selected_vibes: 默认 Vibe
            default_characters: 默认多人物配置

        Returns:
            (是否全部成功, 汇总消息)
        """
        # 解析 JSON 数组
        try:
            items = json.loads(batch_prompts)
        except json.JSONDecodeError as e:
            return False, f"batch_prompts 不是合法 JSON：{e!s}"

        if not isinstance(items, list):
            return False, "batch_prompts 必须是 JSON 数组"
        if not items:
            return False, "batch_prompts 数组不能为空"
        if len(items) > 10:
            return False, "batch_prompts 最多支持 10 张图"

        # 验证每项格式
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                return False, f"batch_prompts[{idx}] 必须是对象"
            if not str(item.get("prompt", "")).strip():
                return False, f"batch_prompts[{idx}].prompt 不能为空"

        logger.info(f"批量画图模式：共 {len(items)} 张图")

        success_count = 0
        fail_count = 0
        results: list[str] = []

        for idx, item in enumerate(items):
            item_prompt = str(item["prompt"]).strip()
            item_resolution = str(item.get("resolution", "")).strip() or default_resolution
            item_negative = str(item.get("negative_prompt", "")).strip() or default_negative_prompt
            item_vibes = str(item.get("selected_vibes", "")).strip() or default_selected_vibes
            item_characters = str(item.get("characters", "")).strip() or default_characters

            logger.info(f"批量画图 [{idx + 1}/{len(items)}]: {item_prompt[:50]}...")

            success, msg = await self._execute_single(
                content_description=item_prompt,
                resolution=item_resolution,
                negative_prompt=item_negative,
                selected_vibes=item_vibes,
                characters=item_characters,
            )

            if success:
                success_count += 1
                results.append(f"第{idx + 1}张：成功")
            else:
                fail_count += 1
                results.append(f"第{idx + 1}张：失败 - {msg}")
                logger.warning(f"批量画图 [{idx + 1}/{len(items)}] 失败: {msg}")

        # 汇总结果
        summary = f"[内部：批量画图完成，成功 {success_count}/{len(items)} 张]"
        if fail_count > 0:
            summary += "\n失败详情：\n" + "\n".join(
                r for r in results if "失败" in r
            )

        return success_count > 0, summary

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

        从插件配置读取 style_reference，
        拼接为：style_reference + content_tags。

        Args:
            content_tags: AI 已转换为英文标签的内容描述

        Returns:
            完整的提示词字符串
        """
        style_ref = ""

        config = getattr(self.plugin, "config", None)
        if config is not None:
            gen = getattr(config, "generation", None)
            if gen is not None:
                style_ref = getattr(gen, "style_reference", "") or ""

        parts = [p for p in [style_ref.strip(), content_tags.strip()] if p]
        return ", ".join(parts)
