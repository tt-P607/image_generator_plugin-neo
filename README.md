# Image Generator Plugin (NovelAI)

基于 NovelAI 官方 API 的 AI 图片生成插件，专为 Neo-MoFox 机器人设计。支持文生图、图生图、自拍生成以及 Vibe 风格参考等功能。

## 🌟 主要功能

- **文生图 (Text-to-Image)**：通过指令或 AI 自动调用生成精美图片。
- **AI 自拍 (Selfie)**：AI 会根据预设的角色特征生成“自己的照片”。
- **图生图 (Image-to-Image)**：基于已有图片进行二次创作（需框架支持引用消息）。
- **Vibe 风格参考**：支持注入参考图来控制生成图片的风格和内容。
- **智能路由**：支持通过 `/` 命令手动触发，也支持 LLM 根据对话语境自动调用（Action）。

## ⚙️ 配置说明

配置文件通常位于 `config/plugins/image_generator_plugin/config.toml`。

### 基础配置
- `plugin.enabled`: 是否启用插件。
- `api.api_keys`: 填入你的 NovelAI API Key（支持多个，自动轮换）。
- `api.proxy`: 如果在国内环境使用，请配置代理地址（如 `http://127.0.0.1:7890`）。

### 生图参数
- `generation.model`: 默认使用 `nai-diffusion-4-5-curated`。
- `generation.resolution`: 默认分辨率（如 `1024x1024`）。
- `generation.character_prompt`: **重要！** 定义 Bot 的外貌特征（如粉发、蓝瞳、精灵耳），用于自拍功能。

### Vibe 预设
在 `plugins/image_generator_plugin/vibes/` 目录下放入参考图，并在配置中添加预设：
```toml
[[vibe.presets]]
file = "style_sample.png"
ie = 1.0
strength = 0.6
```

## 🚀 使用方法

### 1. 聊天指令 (Manual Commands)

| 命令 | 用法示例 | 说明 |
| :--- | :--- | :--- |
| `/nai_image draw` | `/nai_image draw 横图 sunset beach` | 文生图。支持：方图/横图/竖图 |
| `/nai_edit edit` | (引用图片) `/nai_edit edit cat ears 0.6` | 图生图。末尾数字为修改强度 |
| `/nai_vibe list` | `/nai_vibe list` | 查看素材库中的 Vibe 文件 |
| `/nai_vibe status` | `/nai_vibe status` | 查看当前已加载的 Vibe 设置 |
| `/nai_vibe info` | `/nai_vibe info` | 查询 NovelAI 账号剩余点数/订阅状态 |

### 2. AI 自动触发 (Action)

当插件启用且 `components.action_enabled = true` 时，你只需在聊天中对 Bot 说：
- “给我画一个穿着和服的少女”
- “我想看你的自拍”
- “来张风景画看看”

Bot 会自动理解你的意图，调用 `draw_image` 或 `generate_selfie` 动作进行创作。

## 📂 目录结构

- `actions/`: AI 自动调用的动作逻辑。
- `commands/`: 用户手动输入的指令逻辑。
- `services/`: 核心生图服务与 API 交互。
- `vibes/`: 存放 Vibe 参考图素材。
- `temp_images/`: 运行时生成的临时图片存放处。

## ⚠️ 注意事项

1. **API 额度**：生成图片会消耗 NovelAI 的 Anlas 点数或订阅额度。
2. **网络环境**：请确保机器人所在的网络环境可以访问 `image.novelai.net`。
3. **内容安全**：默认配置已包含负面提示词以规避 NSFW 内容，可根据需求在 `generation.negative_prompt` 中调整。
