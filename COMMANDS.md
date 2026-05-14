# 图片生成插件命令手册

> 权限要求：所有命令均需 **OPERATOR（管理员）及以上** 权限，普通用户无法使用。

---

## 中文快捷命令一览

| 中文命令 | 等价英文命令 | 说明 |
|---------|------------|------|
| `/画图 <提示词>` | `/nai_image <提示词>` | 文生图 |
| `/生图 <提示词>` | `/nai_image <提示词>` | 文生图（别名） |
| `/改图 <提示词>` | `/nai_edit <提示词>` | 图生图（需引用图片） |
| `/修图 <提示词>` | `/nai_edit <提示词>` | 图生图（别名） |
| `/参考图 <提示词>` | `/nai_ref <提示词>` | 精确参考图生图（需引用图片） |
| `/风格 列表` | `/nai_vibe list` | 查看可用 Vibe 素材 |
| `/风格 添加 <文件名>` | `/nai_vibe add <文件名>` | 加载 Vibe 素材 |
| `/风格 状态` | `/nai_vibe status` | 查看已加载 Vibe |
| `/风格 清空` | `/nai_vibe clear` | 清空已加载 Vibe |
| `/风格 账号` | `/nai_vibe info` | 查询账号信息 |

> 中文命令与英文命令完全等价，可混用；原有英文命令全部保留。

---

## 目录

- [/nai_image（/画图）— 文生图](#nai_image--文生图)
- [/nai_edit（/改图）— 图生图](#nai_edit--图生图)
- [/nai_ref（/参考图）— 精确参考图生图](#nai_ref--精确参考图生图)
- [/nai_vibe（/风格）— Vibe 参考图管理](#nai_vibe--vibe-参考图管理)
- [画幅参数一览](#画幅参数一览)
- [常见问题](#常见问题)

---

## /nai_image — 文生图

根据文字提示词生成图片。中文别名：`/画图`、`/生图`。

### 语法

```
/nai_image [draw] [画幅] [正面:/正面：] <提示词> [负面:/负面：] <负面提示词> [--scale X] [--rescale X]
/画图 [画幅] [正面:/正面：] <提示词> [负面:/负面：] <负面提示词> [--scale X] [--rescale X]
```

- `draw` 子命令可省略，直接写提示词也能触发。
- `画幅` 可省略，省略时使用配置文件中 `generation.resolution` 的默认值（默认 1024×1024）。
- `正面:` / `正面：` 前缀可省略，直接写提示词即可。半角冒号和全角冒号均支持。
- `负面:` / `负面：` 及之后的内容为负面提示词，可省略（省略时使用配置文件中的通用负面词）。半角冒号和全角冒号均支持。
- `--scale X`：临时覆盖引导比例（Prompt Guidance），省略时使用配置值。
- `--rescale X`：临时覆盖提示词引导重新缩放（cfg_rescale），省略时使用配置值。

### 负面提示词写法

| 格式 | 示例 |
|------|------|
| 仅正面（无负面） | `/画图 1girl, pink hair` |
| 带负面（半角冒号） | `/画图 正面: 1girl 负面: chibi, q版` |
| 带负面（全角冒号） | `/画图 正面：1girl 负面：chibi, q版` |
| 省略正面关键字 | `/画图 1girl, pink hair 负面: chibi` |

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
/画图 1girl, pink hair, blue eyes, masterpiece

# 指定横图
/画图 横图 mountain scenery, sunset

# 指定竖图
/画图 竖图 1girl, spring park, flowers

# 带负面提示词
/画图 正面: 1girl, angry but cute 负面: chibi, deformed, q版

# 省略"正面:"关键字
/画图 1girl, angry but cute 负面: chibi, q版

# 临时指定引导比例
/画图 正面: 1girl, fantasy armor --scale 7

# 同时指定 scale 和 rescale
/画图 正面: 1girl, colorful dress --scale 8 --rescale 0.5

# 全参数组合
/画图 竖图 正面: 1girl, angry but cute 负面: chibi, q版, deformed --scale 7 --rescale 0.3
```

---

## /nai_edit — 图生图

基于一张已有的图片进行编辑，生成新图。中文别名：`/改图`、`/修图`。

> **使用方法**：先引用（回复）一张图片，再发送命令。框架会自动从被引用的消息中提取图片。

### 语法

```
/nai_edit [edit] <提示词> [强度]
/改图 <提示词> [强度]
```

- `edit` 子命令可省略。
- `强度` 为 0.1–0.99 的小数，控制生成结果与原图的相似程度。值越小越接近原图，值越大变化越大。省略时使用配置中的 `advanced.img2img_default_strength`（默认 0.7）。

### 示例

```
# 标准用法（先引用一张图，再发此命令）
/nai_edit edit add a hat, better lighting 0.6
/改图 add a hat, better lighting 0.6

# 省略 edit
/nai_edit make it sunset, warm tones 0.5
/改图 make it sunset, warm tones 0.5

# 强度较低（变化小，更接近原图）
/nai_edit edit refine details, enhance quality 0.3
```

---

## /nai_ref — 精密参考图生图

引用一张图片作为 **精密参考（Director Reference）**，让模型在文生图时参考该图的人物/风格特征，而不是像图生图那样重绘原图。中文别名：`/参考图`。

> **图生图 vs 精密参考的区别**
> - **图生图** (`/改图`)：`action=img2img`，直接在原图基础上重绘，`strength` 控制变化幅度
> - **精密参考** (`/参考图`)：`action=generate`，正常文生图但附带参考约束，效果更自然，可单独控制参考人物或风格

### 语法

```
/nai_ref [ref] [正面:/正面：] <提示词> [负面:/负面：] <负面词> [--type X] [--fidelity X] [--strength X] [--scale X] [--rescale X]
/参考图 <提示词> [--type X] [--fidelity X] [--strength X]
```

| 参数 | 中文别名 | 说明 | 默认值 |
|------|------|------|-------|
| `--type X` | `--参考类型 X` | 参考类型：`角色`/`人物`/`character` \| `风格`/`style` \| `两者`/`全部`/`both`（两者） | `两者` |
| `--fidelity X` | — | 忠实度（0.0–1.0），越高越严格匹配参考图特征 | `1.0` |
| `--strength X` | — | 参考强度（0.0–1.0），越高生成结果越接近参考图风格 | `1.0` |
| `--scale X` | — | 引导比例（同 /画图） | 配置值 |
| `--rescale X` | — | cfg_rescale（同 /画图） | 配置值 |

> **`--type` 详解**
> - `角色`/`人物`/`character`：只参考图中**角色**的外观特征（发色、瞳色、服装等）
> - `风格`/`style`：只参考图中的**画风**（笔触、色调、构图风格等）
> - `两者`/`全部`/`both`（默认）：同时参考**角色**和**画风**

### 示例

```
# 默认参数（先引用图片，再发命令）
/参考图 1girl, fantasy armor

# 只参考人物角色（不影响画风）
/参考图 1girl, beach --type 角色

# 只参考画风（生成新角色但保留画风）
/参考图 1boy, casual outfit --type 风格

# 调整忠实度和参考强度
/参考图 1girl, pink hair --fidelity 0.8 --strength 0.6

# 带负面词
/参考图 正面: 1girl, blue sky 负面: chibi, deformed --type 两者 --strength 0.7
```

---

## /nai_vibe — Vibe 参考图管理

管理用户手动添加的 Vibe 参考图（临时生效，重启后清空）。  
Vibe 文件需事先放在插件目录的 `vibes/` 文件夹中。中文别名：`/风格`。

> Vibe 是 NovelAI 的"参考图风格迁移"功能，可以让生成的图片在风格上参考指定的素材图。

### 子命令

| 英文命令 | 中文命令 | 说明 |
|------|------|------|
| `/nai_vibe list` | `/风格 列表` | 列出 `vibes/` 目录中所有可用的素材文件 |
| `/nai_vibe add <文件名>` | `/风格 添加 <文件名>` | 加载一个 Vibe 素材（当次会话生效） |
| `/nai_vibe status` | `/风格 状态` | 查看当前已加载的 Vibe 列表 |
| `/nai_vibe clear` | `/风格 清空` | 清空当前会话中所有手动加载的 Vibe |
| `/nai_vibe info` | `/风格 账号` | 查询 NovelAI 账号信息（余额等） |

### 示例

```
# 查看可用素材
/nai_vibe list
/风格 列表

# 加载一个 Vibe（文件名含空格时用引号）
/nai_vibe add 速写本风格.naiv4vibe
/风格 添加 速写本风格.naiv4vibe
/风格 添加 "我的风格.naiv4vibe"

# 查看当前加载状态
/nai_vibe status
/风格 状态

# 清空所有已加载的 Vibe
/nai_vibe clear
/风格 清空

# 查询账号信息
/nai_vibe info
/风格 账号
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

## 生图参数配置参考

所有以下参数在 `config/plugins/image_generator_plugin/config.toml` 的 `[generation]` 节中配置，命令中的 `--scale`/`--rescale` 可临时覆盖对应值。

### 采样器（sampler）

| 配置值 | 官网名 | 特性 | 推荐程度 |
|--------|-------|------|---------|
| `k_euler_ancestral` | **Euler Ancestral** | 每步带随机噪声，多样性强，画面活泼 | ⭐⭐⭐ 官网默认，插件默认 |
| `k_euler` | Euler | 确定性，稳定，同 seed 可完全复现 | ⭐⭐ 追求一致性时用 |
| `k_dpmpp_2s_ancestral` | DPM++ 2S Ancestral | 高质量+随机性，耗时较长 | ⭐⭐⭐ 精出图 |
| `k_dpmpp_2m` | DPM++ 2M | 高质量确定性 | ⭐⭐ 质量稳定 |
| `k_dpmpp_2m_sde` | DPM++ 2M SDE | 细节更多的 2M | ⭐⭐ |
| `k_dpmpp_sde` | DPM++ SDE | 细节最丰富，最慢 | ⭐ 细节控 |

### 噪声调度（noise_schedule）

| 配置值 | 说明 | 适用 |
|--------|------|------|
| `karras` | 非线性降噪，整体质量好 | ✅ V4 首选（官网+插件默认） |
| `exponential` | 指数降噪，风格偏柔和 | V4 可选 |
| `polyexponential` | 多项式指数，更平滑 | V4 可选 |
| `native` | V3 专用，V4 不推荐 | V3 固定值（插件自动处理） |

### 引导比例与重新缩放联动

| scale 值 | 推荐 rescale | 效果 |
|----------|------------|------|
| 5.0–6.5 | 0.0–0.3 | 官方推荐区间，画面自然均衡 |
| 6.5–8.0 | 0.3–0.7 | 高引导，颜色更饱和，rescale 防过饱和 |
| 8.0 以上 | 0.5–1.0 | 极度贴合提示词，容易失真 |

> 官网默认 `scale = 6.5`，插件默认 `scale = 5.0`。

### Variety+（variety_plus）

| 配置值 | API 效果 | 说明 |
|--------|---------|------|
| `false`（插件默认） | `skip_cfg_above_sigma = null` | 全程引导，同提示词出图差异小，风格稳定 |
| `true`（官网默认） | `skip_cfg_above_sigma = 19.0` | 主体成形前跳过引导，多样性更强，出图差异大 |

> 同提示词出图差异过大时：先关闭 `variety_plus`，再考虑改用 `k_euler`。

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
