# 每日服装系统设计方案 v1.1

> `image_generator_plugin-neo` 衣柜功能设计文档  
> 状态：**待确认** — 请补充「第七节」中的 outfit_prompt

---

## 一、功能总览

| 项目 | 决策 |
|---|---|
| 生成时机 | 每天定时任务（默认 06:00，可配置） |
| 时间段 | 3 段：白天 06-18、傍晚 18-22、深夜 22-06 |
| 上下文输入 | 当前时段 + 当天日期 + 季节 + 是否节假日/周末 |
| 生成 Prompt | 由用户提供（见第七节留空区域） |
| 注入方式 | 软提示：把当前时段 tags 写入 action description |
| 换装 Action | 不需要（LLM 每日自动生成） |

---

## 二、数据结构

**文件**：`data/image_generator_plugin/daily_outfit.json`

```json
{
  "date": "2026-04-21",
  "generated_at": "2026-04-21T06:00:00",
  "context": {
    "season":    "spring",
    "is_weekend": false,
    "holiday":   null
  },
  "segments": {
    "daytime":  "white blouse, navy pleated skirt, mary jane shoes, ...",
    "evening":  "casual hoodie, loose wide-leg pants, slippers, ...",
    "night":    "white pajamas, fluffy socks, ..."
  }
}
```

若 `date` 不等于今天则视为过期，下次定时任务刷新时重新生成。

---

## 三、上下文感知设计

### 3.1 季节判断

按月份简单划分（无需依赖外部库）：

| 月份 | 季节 |
|---|---|
| 3-5 月 | spring（春） |
| 6-8 月 | summer（夏） |
| 9-11 月 | autumn（秋） |
| 12-2 月 | winter（冬） |

### 3.2 工作日 / 休息日判断

- 首先判断是否周末（周六/日）
- 可选：接入 `holidays` 库检测节假日（`pip install holidays`，国内假期使用 `holidays.China()`）

```python
import datetime
import holidays  # 可选依赖，不安装时降级为只判断周末

def get_day_type(d: datetime.date) -> str:
    """返回 'workday' / 'weekend' / '节日名称'。"""
    if d.weekday() >= 5:
        return "weekend"
    try:
        cn_holidays = holidays.China(years=d.year)
        if d in cn_holidays:
            return cn_holidays[d]  # 节日名称，如 "元旦"
    except Exception:
        pass
    return "workday"
```

> **注意**：`holidays` 是可选依赖。若未安装，只判断周末，不影响基本功能。

### 3.3 时段对应场景参考（传给 LLM 的上下文）

| 时段 | day_type = workday | day_type = weekend | day_type = 节日 |
|---|---|---|---|
| daytime | 上班/上课，正装/制服 | 外出休闲，便装 | 节日外出，节庆风格 |
| evening | 下班回家，居家休闲 | 傍晚散步/聚会 | 节日晚宴/庆祝 |
| night | 睡前，睡衣/家居服 | 睡前，睡衣/家居服 | 同左 |

---

## 四、模块结构

```
image_generator_plugin-neo/
├── wardrobe/
│   ├── __init__.py
│   ├── outfit_manager.py      # 读写 JSON；get_current_tags() / get_current_segment()
│   └── outfit_generator.py   # 上下文组装 + LLM 调用 + 定时任务注册
├── config.py                 # 新增 [wardrobe] section
└── plugin.py                 # on_plugin_loaded 启动定时任务
```

不修改 `base_image_action.py`、`image_service.py`、现有 Vibe 系统、命令系统。

---

## 五、配置方案（`config.py` 新增 `[wardrobe]` section）

```toml
[wardrobe]
# 是否启用每日服装系统
enabled = true

# 每天刷新时间（"HH:MM" 24h 格式）
refresh_time = "06:00"

# 生成所用模型（对应 model.toml 里的 name）；留空使用默认任务模型
generate_model = ""

# 数据文件路径
data_file = "data/image_generator_plugin/daily_outfit.json"

# 时间段边界（小时整数，24h）
daytime_start  = 6
evening_start  = 18
night_start    = 22

# 是否启用节假日感知（需要安装 holidays 库：uv add holidays）
holiday_aware = false
```

---

## 六、核心逻辑伪代码

### 6.1 outfit_manager.py

```python
class OutfitManager:
    def load(self) -> dict | None: ...         # 读 JSON，返回 None 表示今天还没生成
    def save(self, data: dict) -> None: ...    # 写 JSON
    def get_current_segment(self) -> str:     # 根据当前时间返回 "daytime"/"evening"/"night"
    def get_current_tags(self) -> str:        # 返回当前时段 tags，若未生成返回 ""
```

### 6.2 outfit_generator.py

```python
async def generate_daily_outfit(config: WardrobeSection, outfit_prompt: str) -> None:
    """每天执行一次，生成三段服装 tags 并存入 JSON。"""
    today = date.today()

    # 组装上下文
    season   = get_season(today)          # "spring" / "summer" / "autumn" / "winter"
    day_type = get_day_type(today)        # "workday" / "weekend" / "节日名"

    # 调用 LLM（用户提供的 outfit_prompt 作为 system prompt）
    user_message = build_user_message(season, day_type)
    result = await call_llm(
        system=outfit_prompt,
        user=user_message,
        model=config.generate_model,
    )

    # 解析 JSON 响应 {"daytime": "...", "evening": "...", "night": "..."}
    tags = parse_llm_response(result)
    manager.save({
        "date": today.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "context": {"season": season, "is_weekend": today.weekday() >= 5, "holiday": ...},
        "segments": tags,
    })

    # 更新 action description（软注入）
    inject_outfit_hint_to_actions(manager)


def register_daily_scheduler(config: WardrobeSection, outfit_prompt: str) -> None:
    """注册每日定时任务。"""
    h, m = map(int, config.refresh_time.split(":"))
    # 使用 src.app.plugin_system.api 里的 scheduler API（待确认接口）
    get_scheduler().add_daily_job(
        func=lambda: generate_daily_outfit(config, outfit_prompt),
        hour=h,
        minute=m,
        job_id="image_generator_wardrobe_refresh",
    )
```

### 6.3 plugin.py 中新增（on_plugin_loaded）

```python
async def on_plugin_loaded(self) -> None:
    ...
    # 现有初始化代码保持不变
    ...

    # 衣柜系统初始化
    cfg = self.config
    if isinstance(cfg, ImageGeneratorConfig) and cfg.wardrobe.enabled:
        outfit_manager = OutfitManager(cfg.wardrobe.data_file)
        register_daily_scheduler(cfg.wardrobe, cfg.wardrobe.outfit_prompt)

        # 启动时若今天还没生成，立即生成一次
        if outfit_manager.load() is None:
            await generate_daily_outfit(cfg.wardrobe, cfg.wardrobe.outfit_prompt)

        # 将当前时段 tags 注入到 action description
        inject_outfit_hint_to_actions(outfit_manager)
```

### 6.4 软注入示意

```python
def inject_outfit_hint_to_actions(manager: OutfitManager) -> None:
    tags = manager.get_current_tags()
    segment = manager.get_current_segment()
    if not tags:
        return

    SEGMENT_NAMES = {"daytime": "白天", "evening": "傍晚", "night": "深夜"}
    hint = (
        f"\n\n【今日穿搭参考（{SEGMENT_NAMES[segment]}）】\n"
        f"{tags}\n"
        "生成你自己的照片/自拍时，可参考上述服装 tags。"
    )
    DrawAction.action_description = DrawAction._BASE_DESCRIPTION + hint
```

---

## 七、待填写：outfit_prompt（用户提供）

> **请在此处填入你的服装生成 system prompt。**  
> LLM 会在此 prompt 基础上收到如下 user message：

```
今天的背景信息：
- 季节：{season}（春/夏/秋/冬）
- 日期类型：{day_type}（workday / weekend / 节日名）

请为以下三个时段分别生成角色今天会穿的服装 NAI booru-style 英文 tags（每段 8-15 个 tag）：
- 白天（daytime）
- 傍晚（evening）
- 深夜（night）

以 JSON 格式返回：
{"daytime": "tag1, tag2, ...", "evening": "tag1, tag2, ...", "night": "tag1, tag2, ..."}
```

**你的 system prompt（等待填写）**：

```
（你的预设 prompt 填写在此处）
```

---

## 八、实现顺序

1. [ ] `config.py` 新增 `WardrobeSection`
2. [ ] `wardrobe/outfit_manager.py` — 数据读写 + 时段判断
3. [ ] `wardrobe/outfit_generator.py` — 上下文组装 + LLM 调用 + 注入函数
4. [ ] `plugin.py` — `on_plugin_loaded` 集成 + 定时任务注册
5. [ ] （可选）`holidays` 加入依赖：`uv add holidays`
6. [ ] 确认 scheduler API 接口（查 `src/app/plugin_system/api/` 中是否有 scheduler 相关）

---

## 九、待确认事项

| 问题 | 状态 |
|---|---|
| outfit_prompt 由用户提供 | ⏳ 等待 |
| `holidays` 库是否加入依赖 | ⏳ 等待 |
| 是否需要 `/wardrobe refresh` 命令手动强制刷新 | ⏳ 等待 |
| scheduler API 接口确认（是否支持 daily job） | ⏳ 待查代码 |
