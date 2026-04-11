# 图片生成插件命令手册

> 权限要求：所有命令均需 **OPERATOR（管理员）及以上** 权限，普通用户无法使用。

---

## 目录

- [/nai_image — 文生图](#nai_image--文生图)
- [/nai_edit — 图生图](#nai_edit--图生图)
- [/nai_vibe — Vibe 参考图管理](#nai_vibe--vibe-参考图管理)
- [画幅参数一览](#画幅参数一览)
- [常见问题](#常见问题)

---

## /nai_image — 文生图

根据文字提示词生成图片。

### 语法

```
/nai_image [draw] [画幅] <提示词> [---负面提示词]
```

- `draw` 子命令可省略，直接写提示词也能触发。
- `画幅` 可省略，省略时使用配置文件中 `generation.resolution` 的默认值（默认 1024×1024）。
- 负面提示词用 `---` 与正向提示词分隔，可省略（省略时使用配置文件中的通用负面词）。

### 画幅关键字

| 关键字 | 尺寸（宽×高） | 适合场景 |
|--------|------------|---------|
| 方图 / 方 / square | 1024×1024 | 头像、通用 |
| 横图 / 横 / landscape | 1216×832 | 风景、横版 |
| 竖图 / 竖 / portrait | 832×1216 | 人物、竖版 |

也支持直接写尺寸，如 `832x1216`（仅限以上三种）。

### 预设风格

| 关键字 | 尺寸 | 自动添加的提示词 |
|--------|------|----------------|
| 人物 | 832×1216 | masterpiece, best quality, 1girl, ... |
| 风景 | 1216×832 | masterpiece, landscape, scenery, ... |
| 头像 | 1024×1024 | masterpiece, portrait, close-up, ... |

### 示例

```
# 最简单的用法（方图，1024×1024）
/nai_image draw 1girl, pink hair, blue eyes, masterpiece

# 省略 draw，直接触发
/nai_image 美丽的日落，大海，晚霞

# 指定横图
/nai_image draw 横图 mountain scenery, sunset, detailed

# 指定竖图
/nai_image draw 竖图 1girl, spring park, flowers

# 自定义负面提示词（用 --- 分隔）
/nai_image draw 竖图 1girl, pink hair ---nsfw, bad anatomy, extra fingers

# 使用预设风格
/nai_image draw 风景 golden hour, dramatic clouds

# 直接写尺寸
/nai_image draw 1216x832 cyberpunk city, neon lights
```

---

## /nai_edit — 图生图

基于一张已有的图片进行编辑，生成新图。

> **注意**：使用此命令时需要先引用（回复）一张图片，功能依赖框架的引用消息解析，目前处于 TODO 状态，实际不可用。

### 语法

```
/nai_edit [edit] <提示词> [强度]
```

- `edit` 子命令可省略。
- `强度` 为 0.1–0.99 的小数，控制生成结果与原图的相似程度。值越小越接近原图，值越大变化越大。省略时使用配置中的 `advanced.img2img_default_strength`（默认 0.7）。

### 示例

```
# 标准用法（先引用一张图，再发此命令）
/nai_edit edit add a hat, better lighting 0.6

# 省略 edit
/nai_edit make it sunset, warm tones 0.5

# 强度较低（变化小，更接近原图）
/nai_edit edit refine details, enhance quality 0.3
```

---

## /nai_vibe — Vibe 参考图管理

管理用户手动添加的 Vibe 参考图（临时生效，重启后清空）。  
Vibe 文件需事先放在插件目录的 `vibes/` 文件夹中。

> Vibe 是 NovelAI 的"参考图风格迁移"功能，可以让生成的图片在风格上参考指定的素材图。

### 子命令

| 命令 | 说明 |
|------|------|
| `/nai_vibe list` | 列出 `vibes/` 目录中所有可用的素材文件 |
| `/nai_vibe add <文件名>` | 加载一个 Vibe 素材（当次会话生效） |
| `/nai_vibe status` | 查看当前已加载的 Vibe 列表 |
| `/nai_vibe clear` | 清空当前会话中所有手动加载的 Vibe |
| `/nai_vibe info` | 查询 NovelAI 账号信息（余额等） |

### 示例

```
# 查看可用素材
/nai_vibe list

# 加载一个 Vibe（文件名含空格时用引号）
/nai_vibe add 速写本风格.naiv4vibe
/nai_vibe add "我的风格.naiv4vibe"

# 查看当前加载状态
/nai_vibe status

# 清空所有已加载的 Vibe
/nai_vibe clear

# 查询账号信息
/nai_vibe info
```

---

## 画幅参数一览

| 写法 | 实际尺寸 |
|------|---------|
| 方、方图、square | 1024×1024 |
| 横、横图、横版、landscape | 1216×832 |
| 竖、竖图、竖版、portrait | 832×1216 |
| 1024x1024 | 1024×1024 |
| 1216x832 | 1216×832 |
| 832x1216 | 832×1216 |

---

## Vibe 功能说明

插件支持两种 Vibe 注入模式，在配置文件 `config.toml` 的 `[vibe]` 节中配置：

### always 模式（始终注入）

```toml
[vibe]
always_enabled = true

[[vibe.always]]
file = "底图风格.naiv4vibe"
ie = 0.7      # 信息提取量（0.0–1.0），越高越参考原图内容
strength = 0.6 # 参考强度（0.0–1.0），越高风格越接近素材
```

开启后每次生图都会注入列表中的 Vibe，适合固定风格底图。

### selectable 模式（LLM 自选）

```toml
[vibe]
selectable_enabled = true

[[vibe.selectable]]
file = "水彩.naiv4vibe"   # 文件名去掉扩展名后即为画风名（"水彩"）
ie = 1.0
strength = 0.6

[[vibe.selectable]]
file = "赛博朋克.naiv4vibe"
ie = 0.8
strength = 0.7
```

开启后 `draw_image` Action 的描述中会注入可选画风列表，LLM 可根据用户需求自行决定使用哪个或哪几个 Vibe。

---

## 常见问题

**Q：命令没有反应怎么办？**  
A：首先确认自己有 OPERATOR 或以上权限。权限不足时命令会静默拒绝（不回复消息）。

**Q：API 返回 429 Too Many Requests？**  
A：NovelAI 有请求频率限制。插件内置了冷却队列（默认 25 秒冷却），队列中的任务会自动排队等待重试，稍等即可。

**Q：Vibe 文件放哪里？**  
A：放在插件目录下的 `vibes/` 文件夹。支持 `.naiv4vibe`、`.naiv4vibebundle`、`.png`、`.jpg` 格式。

**Q：`/nai_vibe add` 加载的 Vibe 和配置文件里的 `[[vibe.always]]` 有什么区别？**  
A：
- `[[vibe.always]]`：**插件启动时**编码加载，对所有用户永久生效。
- `/nai_vibe add`：**运行时**临时加载，仅对当次会话的 `command_user` 生效，重启后清空。

**Q：提示词用中文还是英文？**  
A：NovelAI 的模型对英文提示词效果更好。中文提示词也能识别，但英文描述通常更精准。
