"""换装工具 — 让模型可以主动从衣柜中选择单品组合换装。

模型可以自由从各槽位选择单品 id，组合成新的当前时段穿搭。
换装后会立即更新状态文件并刷新 DrawAction 的服装提示。
"""

from __future__ import annotations

import json
from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

logger = get_logger("image_generator_plugin.wardrobe_tool")


class WardrobeChangeTool(BaseTool):
    """换装工具 — 从衣柜槽位中自由选择单品组合换装。"""

    tool_name: str = "change_outfit"
    tool_description: str = (
        "从衣柜中选择单品组合换装，立即更新当前时段的穿搭。\n"
        "传入 JSON 对象字符串，key 为槽位名称，value 为该槽位选择的单品 id。\n"
        "不需要填满所有槽位，只填想换的部分即可（未填的槽位保持不变）。\n"
        "换装后画图时会自动使用新的服装标签。"
    )

    async def execute(
        self,
        outfit_selection: Annotated[
            str,
            "JSON 对象字符串，key 为槽位名称，value 为单品 id。"
            '示例：{"上衣": "pink_hoodie", "下装": "black_jeans", "鞋子": "sneakers_white"}'
            "可用的槽位和单品 id 参见衣柜系统注入的当前穿搭信息。",
        ],
    ) -> tuple[bool, str]:
        """执行换装操作。"""
        # 解析 JSON
        try:
            selection = json.loads(outfit_selection)
        except json.JSONDecodeError as e:
            return False, f"outfit_selection 不是合法 JSON：{e!s}"

        if not isinstance(selection, dict):
            return False, "outfit_selection 必须是 JSON 对象"
        if not selection:
            return False, "outfit_selection 不能为空，请至少选择一个槽位的单品"

        # 获取 wardrobe_manager
        wardrobe_manager = getattr(self.plugin, "_wardrobe_manager", None)
        if not wardrobe_manager:
            return False, "衣柜系统未启用或未初始化"

        # 获取当前时段
        segment = wardrobe_manager.get_current_segment()

        # 验证并应用每个槽位选择
        wardrobe = wardrobe_manager.load_wardrobe()
        applied: list[str] = []
        errors: list[str] = []

        for slot_name, item_id in selection.items():
            if not isinstance(slot_name, str) or not isinstance(item_id, str):
                errors.append(f"无效的键值对：{slot_name}={item_id}")
                continue

            # 检查槽位是否存在
            if slot_name not in wardrobe["slots"]:
                errors.append(f"槽位 '{slot_name}' 不存在")
                continue

            # 检查单品 id 是否存在于该槽位
            items = wardrobe["slots"][slot_name]
            matched_item = None
            for item in items:
                if item["id"] == item_id:
                    matched_item = item
                    break

            if not matched_item:
                errors.append(f"槽位 '{slot_name}' 中不存在单品 '{item_id}'")
                continue

            # 应用到当前时段
            ok = wardrobe_manager.override_segment_slot(segment, slot_name, item_id)
            if ok:
                applied.append(f"{slot_name}: {matched_item.get('name', item_id)}")
            else:
                errors.append(f"应用 {slot_name}={item_id} 失败")

        if not applied:
            return False, "换装失败：" + "；".join(errors)

        # 刷新 DrawAction 的服装提示
        try:
            from ..wardrobe.outfit_generator import inject_outfit_hint
            from ..actions.draw_action import DrawAction
            inject_outfit_hint(wardrobe_manager, DrawAction)
        except Exception as e:
            logger.warning(f"刷新服装提示失败（非致命）: {e}")

        # 构建结果消息
        from ..wardrobe.outfit_manager import SEGMENT_DISPLAY_NAMES
        segment_name = SEGMENT_DISPLAY_NAMES.get(segment, segment)
        result_parts = [f"已换装（{segment_name}）："]
        result_parts.extend(f"  - {item}" for item in applied)
        if errors:
            result_parts.append(f"部分失败：{'；'.join(errors)}")

        # 返回当前完整穿搭 tags
        current_tags = wardrobe_manager.get_current_tags()
        if current_tags:
            result_parts.append(f"当前完整服装标签：{current_tags}")

        return True, "\n".join(result_parts)
