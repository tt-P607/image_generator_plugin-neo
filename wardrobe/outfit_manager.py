"""每日服装数据管理器（Slot 模式）。

用户可自定义槽位名称（如"上衣"、"下装"、"外套"、"帽子"等），
每个槽位包含若干可选单品，LLM 从各槽位各选一个拼成完整穿搭。

数据文件格式（wardrobe.json）：
    {
      "slots": {
        "上衣": [
          {"id": "white_blouse", "name": "白色衬衫", "tags": "white blouse, button up"}
        ],
        "下装": [...]
      },
      "presets": {
        "jk_set": {
          "name": "JK 制服套装",
          "description": "经典JK风格",
          "slots": {"上衣": "white_blouse", "下装": "navy_skirt"}
        }
      }
    }

状态文件格式（wardrobe_state.json）：
    {
      "date": "2025-05-05",
      "daytime": {"上衣": "white_blouse", "下装": "navy_skirt"},
      "evening": {"上衣": "pink_hoodie"},
      "night": {},
      "context": {"season": "spring", "day_type": "workday", ...}
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

SEGMENT_DAYTIME = "daytime"
SEGMENT_EVENING = "evening"
SEGMENT_NIGHT = "night"

SEGMENT_DISPLAY_NAMES: dict[str, str] = {
    SEGMENT_DAYTIME: "白天",
    SEGMENT_EVENING: "傍晚",
    SEGMENT_NIGHT: "深夜",
}

ALL_SEGMENTS = frozenset({SEGMENT_DAYTIME, SEGMENT_EVENING, SEGMENT_NIGHT})


class SlotItem(TypedDict):
    """单个槽位选项。"""

    id: str
    name: str
    tags: str


class PresetEntry(TypedDict):
    """整套穿搭预设。"""

    name: str
    description: str
    slots: dict[str, str]  # slot_name -> item_id


class WardrobeData(TypedDict):
    """衣柜定义文件完整结构。"""

    slots: dict[str, list[SlotItem]]   # slot_name -> [SlotItem, ...]
    presets: dict[str, PresetEntry]    # preset_id -> PresetEntry


class SegmentSelection(TypedDict):
    """单时段的穿搭选择。"""

    # slot_name -> item_id（可以不包含某些槽位）
    pass


class DailyState(TypedDict):
    """每日穿搭状态。"""

    date: str
    daytime: dict[str, str]   # slot_name -> item_id
    evening: dict[str, str]
    night: dict[str, str]
    context: dict             # 生成时的上下文（season、day_type 等）


_DEFAULT_WARDROBE: WardrobeData = {
    "slots": {
        "上衣": [
            {"id": "white_blouse", "name": "白色衬衫", "tags": "white blouse, button up shirt"},
            {"id": "pink_hoodie", "name": "粉色卫衣", "tags": "pink hoodie, casual wear"},
        ],
        "下装": [
            {"id": "navy_skirt", "name": "藏青百褶裙", "tags": "navy pleated skirt, school uniform style"},
            {"id": "denim_shorts", "name": "牛仔短裤", "tags": "denim shorts"},
        ],
        "外套": [
            {"id": "wool_coat", "name": "羊毛大衣", "tags": "long wool coat, winter outerwear"},
        ],
        "鞋子": [
            {"id": "loafers", "name": "乐福鞋", "tags": "loafers, leather shoes"},
            {"id": "sneakers", "name": "运动鞋", "tags": "sneakers, casual shoes"},
        ],
        "配饰": [
            {"id": "hair_ribbon", "name": "发带", "tags": "hair ribbon, hair accessory"},
        ],
    },
    "presets": {
        "jk_set": {
            "name": "JK 制服套装",
            "description": "经典 JK 风格，适合日常出行",
            "slots": {
                "上衣": "white_blouse",
                "下装": "navy_skirt",
                "鞋子": "loafers",
                "配饰": "hair_ribbon",
            },
        }
    },
}


class OutfitManager:
    """衣柜数据管理器（Slot 模式）。

    Args:
        wardrobe_file: 衣柜定义文件路径
        state_file: 每日状态文件路径
        daytime_start: 白天时段起始小时
        evening_start: 傍晚时段起始小时
        night_start: 深夜时段起始小时
    """

    def __init__(
        self,
        wardrobe_file: str | Path,
        state_file: str | Path,
        *,
        daytime_start: int = 6,
        evening_start: int = 18,
        night_start: int = 22,
    ) -> None:
        """初始化衣柜管理器。"""
        self.wardrobe_file = Path(wardrobe_file)
        self.state_file = Path(state_file)
        self.daytime_start = daytime_start
        self.evening_start = evening_start
        self.night_start = night_start

    # ── 衣柜定义（wardrobe.json）──────────────────────────────

    def load_wardrobe(self) -> WardrobeData:
        """加载衣柜定义，不存在时创建默认示例文件。"""
        self.wardrobe_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.wardrobe_file.exists():
            self._write_wardrobe(_DEFAULT_WARDROBE)
            return dict(_DEFAULT_WARDROBE)  # type: ignore[return-value]
        try:
            data = json.loads(self.wardrobe_file.read_text(encoding="utf-8"))
            if "slots" not in data:
                data["slots"] = {}
            if "presets" not in data:
                data["presets"] = {}
            return data
        except Exception:
            return dict(_DEFAULT_WARDROBE)  # type: ignore[return-value]

    def _write_wardrobe(self, data: WardrobeData) -> None:
        self.wardrobe_file.parent.mkdir(parents=True, exist_ok=True)
        self.wardrobe_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_slot_names(self) -> list[str]:
        """返回所有已定义的槽位名称。"""
        return list(self.load_wardrobe()["slots"].keys())

    def get_slot_items(self, slot_name: str) -> list[SlotItem]:
        """返回指定槽位的所有单品。"""
        wardrobe = self.load_wardrobe()
        return wardrobe["slots"].get(slot_name, [])

    def get_item_by_id(self, slot_name: str, item_id: str) -> SlotItem | None:
        """在指定槽位中根据 id 查找单品。"""
        for item in self.get_slot_items(slot_name):
            if item["id"] == item_id:
                return item
        return None

    def find_item_globally(self, item_id: str) -> tuple[str, SlotItem] | None:
        """在所有槽位中查找 item_id，返回 (slot_name, item) 或 None。"""
        wardrobe = self.load_wardrobe()
        for slot_name, items in wardrobe["slots"].items():
            for item in items:
                if item["id"] == item_id:
                    return slot_name, item
        return None

    def get_presets(self) -> dict[str, PresetEntry]:
        """返回所有整套预设。"""
        return self.load_wardrobe().get("presets", {})

    # ── 每日状态（wardrobe_state.json）──────────────────────────

    def load_state(self) -> DailyState | None:
        """加载今日状态，失败返回 None。"""
        if not self.state_file.exists():
            return None
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_state(self, state: DailyState) -> None:
        """保存今日状态，同时保留历史记录。

        状态文件格式扩展为包含 history 列表，保存最近的每日穿搭记录。
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # 读取现有文件以保留历史
        existing: dict = {}
        if self.state_file.exists():
            try:
                existing = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        # 将旧的当天记录归档到 history（如果日期不同）
        history: list[dict] = existing.get("history", [])
        old_date = existing.get("date", "")
        if old_date and old_date != state.get("date"):
            # 把旧记录存入历史
            old_entry = {
                "date": old_date,
                "daytime": existing.get("daytime", {}),
                "evening": existing.get("evening", {}),
                "night": existing.get("night", {}),
                "context": existing.get("context", {}),
            }
            history.append(old_entry)

        # 保留最近 30 天历史（硬上限，防止文件无限增长）
        history = history[-30:]

        # 写入新状态 + 历史
        output = dict(state)
        output["history"] = history
        self.state_file.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_today(self) -> bool:
        """检查状态文件中的日期是否为今天。"""
        state = self.load_state()
        if not state:
            return False
        return state.get("date") == datetime.now().date().isoformat()

    def override_segment_slot(
        self,
        segment: str,
        slot_name: str,
        item_id: str,
    ) -> bool:
        """覆盖某时段某槽位的选择。"""
        if segment not in ALL_SEGMENTS:
            return False
        state = self.load_state()
        if state is None:
            state = cast(DailyState, {
                "date": datetime.now().date().isoformat(),
                "daytime": {},
                "evening": {},
                "night": {},
                "context": {"manual": True},
            })
        state[segment][slot_name] = item_id  # type: ignore[literal-required]
        self.save_state(state)
        return True

    def apply_preset(self, preset_id: str, segment: str) -> bool:
        """将整套预设应用到某时段。"""
        if segment not in ALL_SEGMENTS:
            return False
        presets = self.get_presets()
        if preset_id not in presets:
            return False
        preset_slots = presets[preset_id].get("slots", {})
        state = self.load_state()
        if state is None:
            state = cast(DailyState, {
                "date": datetime.now().date().isoformat(),
                "daytime": {},
                "evening": {},
                "night": {},
                "context": {"manual": True},
            })
        state[segment] = dict(preset_slots)  # type: ignore[literal-required]
        self.save_state(state)
        return True

    # ── 时段 & Tags ──────────────────────────────────────────

    def get_current_segment(self) -> str:
        """根据当前时间返回时段名称。"""
        hour = datetime.now().hour
        if hour >= self.night_start:
            return SEGMENT_NIGHT
        if hour >= self.evening_start:
            return SEGMENT_EVENING
        if hour >= self.daytime_start:
            return SEGMENT_DAYTIME
        return SEGMENT_NIGHT

    def resolve_selection_to_tags(self, selection: dict[str, str]) -> str:
        """将时段选择（slot_name -> item_id）解析为拼接好的 tags 字符串。"""
        wardrobe = self.load_wardrobe()
        tag_parts: list[str] = []
        for slot_name, item_id in selection.items():
            items = wardrobe["slots"].get(slot_name, [])
            for item in items:
                if item["id"] == item_id:
                    tags = item.get("tags", "").strip()
                    if tags:
                        tag_parts.append(tags)
                    break
        return ", ".join(tag_parts)

    def get_current_tags(self) -> str:
        """返回当前时段的服装 tags，无数据时返回空字符串。"""
        state = self.load_state()
        if not state:
            return ""
        segment = self.get_current_segment()
        selection: dict[str, str] = state.get(segment, {})  # type: ignore[assignment]
        return self.resolve_selection_to_tags(selection)

    def get_all_segments_summary(self) -> str:
        """返回今天三时段穿搭的可读摘要。"""
        state = self.load_state()
        if not state:
            return "今天还没有穿搭计划，请先配置衣柜（wardrobe.json）或使用 /nai_wardrobe refresh 生成。"

        date_str = state.get("date", "未知")
        ctx = state.get("context", {})
        ctx_str = ""
        if ctx.get("season") or ctx.get("day_type"):
            ctx_str = f"（{ctx.get('season', '')} · {ctx.get('day_type', '')}）"

        lines = [f"📅 {date_str} 今日穿搭{ctx_str}\n"]
        wardrobe = self.load_wardrobe()

        for segment, display_name in SEGMENT_DISPLAY_NAMES.items():
            selection: dict[str, str] = state.get(segment, {})  # type: ignore[assignment]
            if not selection:
                lines.append(f"  {display_name}：（未设置）")
                continue
            lines.append(f"  {display_name}：")
            for slot_name, item_id in selection.items():
                items = wardrobe["slots"].get(slot_name, [])
                item_name = item_id
                tags = ""
                for item in items:
                    if item["id"] == item_id:
                        item_name = item.get("name", item_id)
                        tags = item.get("tags", "")
                        break
                lines.append(f"    [{slot_name}] {item_name}：{tags[:60]}")

        return "\n".join(lines)

    def get_recent_history(self, days: int = 3) -> list[dict]:
        """获取最近 N 天的穿搭历史记录。

        Args:
            days: 要获取的历史天数

        Returns:
            历史记录列表，每项包含 date/daytime/evening/night 字段，
            按日期从旧到新排列。空列表表示无历史。
        """
        if days <= 0:
            return []
        if not self.state_file.exists():
            return []
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        history: list[dict] = data.get("history", [])
        return history[-days:]
