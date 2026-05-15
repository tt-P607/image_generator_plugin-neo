# Image Generator Plugin (NovelAI)

基于 NovelAI 官方 API 的 AI 图片生成插件，专为 Neo-MoFox 机器人设计。支持文生图、图生图、自拍生成以及 Vibe 风格参考等功能。

## 主要功能

- **文生图 (Text-to-Image)**：通过指令或 AI 自动调用生成精美图片。
- **AI 自拍 (Selfie)**：AI 会根据预设的角色特征生成"自己的照片"。
- **图生图 (Image-to-Image)**：基于已有图片进行二次创作（需框架支持引用消息）。
- **精密参考图 (Director Reference)**：引用图片作为参考约束，效果比图生图更自然。
- **Vibe 风格参考**：支持注入参考图来控制生成图片的风格和内容。
- **多人物 (Multi-Character)**：V4 模型支持最多 6 个角色精确定位。
- **每日服装系统**：LLM 根据季节/日期自动选择三时段搭配。

## 配置说明

配置文件位于 `config/plugins/image_generator_plugin/config.toml`。

### 基础配置
- `plugin.enabled`: 是否启用插件
- `api.api_keys`: NovelAI API Key（支持多个，自动轮换）
- `api.proxy`: 代理地址（如 `http://127.0.0.1:7890`）

### 生图参数
- `generation.model`: 绘图模型（默认 `nai-diffusion-4-5-curated`）
- `generation.style_reference`: 画风标签，自动拼接到提示词最前面
- `generation.character_prompt`: 角色外观描述（自由文本），画自己时参考

### 数据目录
生成的图片和 Vibe 素材存放在 `data/image_generator_plugin/` 下：
- `vibes/` — Vibe 素材文件
- `temp_images/` — AI 对话生成的临时图片
- `command_images/` — 命令生成的图片

---

## 命令手册

> 权限要求：所有命令均需 **OPERATOR（管理员）及以上** 权限。

### 中文快捷命令一览

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

### /nai_image（/画图）— 文生图

```
/画图 [画幅] [正面:] <提示词> [负面:] <负面提示词> [--scale X] [--rescale X]
```

- 画幅：方图/横图/竖图（或 1024x1024/1216x832/832x1216）
- `--scale X`：临时覆盖引导比例
- `--rescale X`：临时覆盖 cfg_rescale

示例：
```
/画图 竖图 1girl, pink hair, blue eyes
/画图 正面: 1girl, fantasy armor 负面: chibi, deformed --scale 7
```

### /nai_edit（/改图）— 图生图

> 先引用一张图片，再发送命令。

```
/改图 <提示词> [强度]
```

强度为 0.1–0.99，越小越接近原图。默认 0.7。

### /nai_ref（/参考图）— 精密参考图生图

> 先引用一张图片，再发送命令。

```
/参考图 <提示词> [--type 角色|风格|两者] [--fidelity X] [--strength X]
```

- `--type`：角色（只参考人物）/ 风格（只参考画风）/ 两者（默认）
- `--fidelity`：忠实度 0.0–1.0（默认 1.0）
- `--strength`：参考强度 0.0–1.0（默认 1.0）

### /nai_vibe（/风格）— Vibe 管理

| 命令 | 说明 |
|------|------|
| `/风格 列表` | 列出 vibes/ 目录中所有可用素材 |
| `/风格 添加 <文件名>` | 加载一个 Vibe（当次会话生效） |
| `/风格 状态` | 查看当前已加载的 Vibe |
| `/风格 清空` | 清空所有手动加载的 Vibe |
| `/风格 账号` | 查询 NovelAI 账号余额 |

---

## 画幅参数

| 关键字 | 尺寸 | 适合场景 |
|--------|------|---------|
| 方图 / square | 1024×1024 | 头像、通用 |
| 横图 / landscape | 1216×832 | 风景、横版 |
| 竖图 / portrait | 832×1216 | 人物、竖版 |

---

## 多人物（Multi-Character）

仅 V4 系列模型支持，最多 6 个角色。LLM 调用 `draw_image` 时传入 `characters` JSON 数组：

```json
[
  {"prompt": "1girl, blonde, source#hugging", "x": 0.3, "y": 0.5},
  {"prompt": "1girl, black hair, target#hugging", "x": 0.7, "y": 0.5}
]
```

互动语法：`source#动作` / `target#动作` / `mutual#动作`（成对使用）。

---

## 生图参数参考

### 采样器

| 配置值 | 特性 |
|--------|------|
| `k_euler_ancestral` | 多样性强，官网默认 |
| `k_euler` | 确定性，同 seed 可复现 |
| `k_dpmpp_2s_ancestral` | 高质量+随机性 |
| `k_dpmpp_2m` | 高质量确定性 |

### 引导比例与 rescale 联动

| scale | 推荐 rescale | 效果 |
|-------|------------|------|
| 5.0–6.5 | 0.0–0.3 | 画面自然均衡 |
| 6.5–8.0 | 0.3–0.7 | 颜色更饱和 |
| 8.0+ | 0.5–1.0 | 极度贴合提示词 |

---

## 常见问题

**Q：命令没有反应？**
A：确认有 OPERATOR 或以上权限。

**Q：API 返回 429？**
A：插件内置冷却队列，任务会自动排队重试。

**Q：Vibe 文件放哪里？**
A：放在 `data/image_generator_plugin/vibes/`。支持 `.naiv4vibe`、`.naiv4vibebundle`、`.png`、`.jpg`。

**Q：提示词用中文还是英文？**
A：NovelAI 只支持使用英文 tag。
