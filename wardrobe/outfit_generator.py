"""每日服装生成器（Slot 模式）。

LLM 根据今日季节/场合，从衣柜各槽位中各选一个单品组成三时段穿搭，
将选定的 {slot_name: item_id} 映射保存到状态文件。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import LLMPayload, ROLE, TaskType, Text
from src.core.config import get_core_config
from src.kernel.scheduler import TriggerType, get_unified_scheduler

from .outfit_manager import (
    OutfitManager,
    SEGMENT_DAYTIME,
    SEGMENT_DISPLAY_NAMES,
    SEGMENT_EVENING,
    SEGMENT_NIGHT,
    DailyState,
    WardrobeData,
)

if TYPE_CHECKING:
    from ..config import ImageGeneratorConfig

logger = get_logger("image_generator_plugin.wardrobe")

_WARDROBE_HINT_MARKER = "【今日穿搭参考"

_SEASONS: dict[int, str] = {
    1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter",
}

_SEASON_DISPLAY: dict[str, str] = {
    "spring": "春季",
    "summer": "夏季",
    "autumn": "秋季",
    "winter": "冬季",
}


def get_season(d: datetime | None = None) -> str:
    """根据月份返回季节名称。"""
    month = (d or datetime.now()).month
    return _SEASONS.get(month, "spring")


def get_day_type(d: datetime | None = None) -> str:
    """返回日期类型：'workday'、'weekend' 或节假日名。"""
    target = (d or datetime.now()).date()
    if target.weekday() >= 5:
        return "weekend"
    try:
        import holidays  # type: ignore[import-untyped]
        cn_holidays = holidays.China(years=target.year)
        if target in cn_holidays:
            return cn_holidays[target]
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"节假日检测失败（非致命）: {e}")
    return "workday"


def _get_model_set(generate_model: str) -> llm_api.ModelSet:
    """根据配置选择模型集。"""
    if generate_model.strip():
        return llm_api.get_model_set_by_name(generate_model.strip())
    return llm_api.get_model_set_by_task(TaskType.ACTOR.value)


def _strip_json_code_fence(raw: str) -> str:
    """剥离模型返回的可选 JSON 代码块包装。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _build_slots_text(wardrobe: WardrobeData) -> str:
    """格式化衣柜槽位列表为 LLM 可读文字。"""
    lines: list[str] = []
    for slot_name, items in wardrobe["slots"].items():
        lines.append(f'槽位 "{slot_name}"：')
        for item in items:
            tags_preview = item.get("tags", "")[:60]
            lines.append(f'  - id: {item["id"]!r}  名称: {item.get("name", "")}  tags: {tags_preview}')
    return "\n".join(lines)


def _validate_selection(
    selection: dict,
    wardrobe: WardrobeData,
    segment_label: str,
) -> dict[str, str]:
    """验证 LLM 返回的 {slot_name: item_id} 映射，过滤掉无效项。"""
    valid: dict[str, str] = {}
    if not isinstance(selection, dict):
        logger.warning(f"{segment_label}: 选择结果格式非 dict，已跳过")
        return valid
    for slot_name, item_id in selection.items():
        if not isinstance(slot_name, str) or not isinstance(item_id, str):
            continue
        items = wardrobe["slots"].get(slot_name, [])
        matched = any(item["id"] == item_id for item in items)
        if matched:
            valid[slot_name] = item_id
        else:
            logger.warning(
                f"{segment_label}: 槽位 {slot_name!r} 中不存在 id {item_id!r}，已跳过"
            )
    return valid


async def generate_daily_outfit(
    manager: OutfitManager,
    config: "ImageGeneratorConfig",
) -> bool:
    """调用 LLM 从衣柜槽位中选择今天三时段的穿搭，保存到状态文件。

    Args:
        manager: OutfitManager 实例
        config: 插件配置

    Returns:
        True 表示成功，False 表示失败
    """
    now = datetime.now()
    today = now.date().isoformat()
    season = get_season(now)
    day_type = get_day_type(now)
    season_display = _SEASON_DISPLAY.get(season, season)

    wardrobe = manager.load_wardrobe()
    if not wardrobe["slots"]:
        logger.warning("衣柜槽位为空，跳过生成")
        return False

    # 读取全局人设
    try:
        core_personality = get_core_config().personality
        parts: list[str] = []
        if core_personality.nickname:
            parts.append(f"角色名称：{core_personality.nickname}")
        if core_personality.personality_core:
            parts.append(f"核心人格：{core_personality.personality_core}")
        if core_personality.identity:
            parts.append(f"身份/外貌：{core_personality.identity}")
        personality_text = "\n".join(parts)
    except Exception:
        personality_text = ""

    day_type_display = {"workday": "工作日", "weekend": "周末"}.get(day_type, f"节假日（{day_type}）")

    system_prompt = (
        "你是一位穿搭顾问。你需要根据今天的季节和日期，从给定的衣柜槽位中为角色选择每个时段（白天/傍晚/深夜）的穿搭。\n"
        "每个槽位从给定选项中选一个 id（也可以不选该槽位，省略即可）。\n"
        "以 JSON 格式返回，结构为：\n"
        '{"daytime": {"槽位名": "item_id", ...}, "evening": {...}, "night": {...}}\n'
        "不要包含任何说明文字，只返回 JSON。"
    )

    slots_text = _build_slots_text(wardrobe)
    user_message_parts = [
        "今天的背景信息：",
        f"- 日期：{today}",
        f"- 季节：{season_display}",
        f"- 日期类型：{day_type_display}",
    ]
    if personality_text:
        user_message_parts += ["", "角色信息：", personality_text]
    user_message_parts += [
        "",
        "衣柜内容（槽位 → 单品列表）：",
        slots_text,
        "",
        "请为白天（daytime）、傍晚（evening）、深夜（night）三个时段各选一套穿搭，",
        "每个时段从各槽位中选择适合的单品 id（不需要的槽位可省略）。",
    ]
    user_message = "\n".join(user_message_parts)

    try:
        model_set = _get_model_set(config.wardrobe.generate_model)
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name="image_generator_wardrobe_select",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_message)))
        response = await request.send(stream=False)
        raw_text: str = await response
    except Exception as e:
        logger.error(f"服装选择 LLM 调用失败: {e}")
        return False

    cleaned = _strip_json_code_fence(raw_text)
    try:
        parsed = json.loads(cleaned)
    except Exception as e:
        logger.warning(f"服装选择 JSON 解析失败: {e}  raw={raw_text[:200]}")
        return False

    if not isinstance(parsed, dict):
        logger.warning(f"服装选择返回格式非 dict: {type(parsed)}")
        return False

    state: DailyState = {
        "date": today,
        "daytime": _validate_selection(parsed.get("daytime", {}), wardrobe, "白天"),
        "evening": _validate_selection(parsed.get("evening", {}), wardrobe, "傍晚"),
        "night": _validate_selection(parsed.get("night", {}), wardrobe, "深夜"),
        "context": {
            "season": season,
            "day_type": day_type,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }

    manager.save_state(state)
    logger.info(
        f"今日穿搭已选定 ({today})："
        f"白天={list(state['daytime'].keys())} "
        f"傍晚={list(state['evening'].keys())} "
        f"深夜={list(state['night'].keys())}"
    )
    return True


def inject_outfit_hint(manager: OutfitManager, draw_action_class: type) -> None:
    """将当前时段服装 tags 软注入到 DrawAction 的 action_description。

    每次调用都会先移除旧的衣柜提示块（如有），再追加最新内容。
    """
    tags = manager.get_current_tags()
    segment = manager.get_current_segment()
    segment_name = SEGMENT_DISPLAY_NAMES.get(segment, segment)

    desc: str = draw_action_class.action_description
    marker_idx = desc.find(_WARDROBE_HINT_MARKER)
    if marker_idx != -1:
        desc = desc[:marker_idx].rstrip()

    if not tags:
        draw_action_class.action_description = desc
        return

    hint = (
        f"\n\n{_WARDROBE_HINT_MARKER}（{segment_name}）】\n"
        f"你今天{segment_name}穿的是：{tags}\n"
        "这是根据今天的日期、季节以及当前时段，从你的衣柜中选好的服装 tags，也就是你当前时段穿的衣服。\n"
        "生成你自己当前的照片或自拍时，请务必在提示词中加入上述服装 tags，以确保图片中的你穿着符合当前时段的打扮。"
    )
    draw_action_class.action_description = desc + hint
    logger.info(f"已注入穿搭提示到 DrawAction（{segment_name}）：{tags[:80]}…")


async def register_wardrobe_scheduler(
    manager: OutfitManager,
    config: "ImageGeneratorConfig",
    draw_action_class: type,
) -> None:
    """注册每小时检查并在需要时刷新今日服装的定时任务。"""
    try:
        h_str, m_str = config.wardrobe.refresh_time.split(":")
        refresh_hour = int(h_str.strip())
        refresh_minute = int(m_str.strip())
    except Exception:
        logger.warning(f"refresh_time 格式无效：{config.wardrobe.refresh_time!r}，使用默认值 06:00")
        refresh_hour, refresh_minute = 6, 0

    async def _check_and_refresh() -> None:
        if manager.is_today():
            inject_outfit_hint(manager, draw_action_class)
            return
        now = datetime.now()
        if now.hour < refresh_hour or (now.hour == refresh_hour and now.minute < refresh_minute):
            return
        ok = await generate_daily_outfit(manager, config)
        if ok:
            inject_outfit_hint(manager, draw_action_class)

    scheduler = get_unified_scheduler()
    await scheduler.create_schedule(
        callback=_check_and_refresh,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": 3600},
        is_recurring=True,
        task_name="image_generator_wardrobe_check",
        force_overwrite=True,
    )
    logger.info("每日穿搭检查任务已注册（每小时触发一次）")
