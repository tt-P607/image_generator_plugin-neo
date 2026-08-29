# NovelAI 绘图插件

让 Neo-MoFox 可以在聊天中使用 NovelAI 画图、改图和处理图片。

插件既可以直连 NovelAI 官方 API，也可以连接参数兼容的 NovelAI Gateway。用户可以通过聊天命令主动生图，AI 也可以在对话中自动调用绘图、局部重绘和图片处理能力。

## 可以做什么

- 根据文字描述生成图片。
- 引用已有图片进行整图重绘。
- 使用人物或画风参考图生成新图片。
- 使用 Vibe 素材控制画风和内容倾向。
- 在同一张图片中安排多个角色及其位置。
- 对图片指定区域进行局部重绘。
- 清理杂物、移除背景、提取线稿、转换草图、线稿上色和改变表情。
- 通过 WebUI 调整常用配置并测试生图。

## 使用前准备

配置文件位于：

```text
config/plugins/image_generator_plugin-neo/config.toml
```

至少需要配置一个 NovelAI Token：

```toml
[api]
api_keys = ["pst-xxxxxxxx"]
```

### 直连官方 API

```toml
[api]
channel = "official"
base_url = "https://image.novelai.net/ai/generate-image"
api_base_url = "https://api.novelai.net"
```

### 使用 NovelAI Gateway

```toml
[api]
channel = "gateway"
base_url = "http://127.0.0.1:8000"
```

Gateway 需要支持本插件使用的 OpenAI 图片扩展接口，包括文生图、图生图、局部重绘、Vibe 和 Director 工具。

## 常用配置

### 模型和画幅

```toml
[generation]
model = "nai-diffusion-5-curated"
available_models = [
	"nai-diffusion-5-curated",
	"nai-diffusion-5-full",
	"nai-diffusion-4-5-full",
]
resolution = "1024x1024"
steps = 28
scale = 5.0
sampler = "k_euler_ancestral"
noise_schedule = "karras"
prompt_guidance_rescale = 0.0
```

`available_models` 是 Bot 和 WebUI 可在单次调用中选择的严格白名单，非空时必须包含默认 `model`。留空则只允许默认模型。插件当前支持：

| 模型 | 提示词与主要能力 | 限制 |
|---|---|---|
| `nai-diffusion-5-full` / `nai-diffusion-5-curated` | 1471 Tokens；英文 Tag 加中、日、英文自然语言；引号文字、原生 Alpha、控制词、视觉小说资产和漫画 | 不支持 Vibe 与 Director Reference |
| `nai-diffusion-4-5-full` / `nai-diffusion-4-5-curated` | 505 Tokens；稳定的英文 Tag 工作流；支持 Vibe 与 Director Reference | 不支持 V5 原生 Alpha、控制词和多语言自然语言工作流 |

Full 更适合精细控制，Curated 更偏稳定和审美一致。局部重绘会自动映射到同代 Inpainting 模型，不能把 `*-inpainting` 直接写入白名单。

推荐画幅：

| 用途 | 尺寸 |
|---|---|
| 方图、头像 | `1024x1024` |
| 人物竖图 | `832x1216` |
| 风景横图 | `1216x832` |

可选采样器：

| 采样器 ID | 说明 |
|-----------|------|
| `k_euler` | Euler |
| `k_euler_ancestral` | Euler Ancestral（默认，推荐） |
| `k_dpm_2` | DPM2 |
| `k_dpm_2_ancestral` | DPM2 Ancestral |
| `k_dpmpp_2m` | DPM++ 2M |
| `k_dpmpp_2m_sde` | DPM++ 2M SDE |
| `k_dpmpp_2s_ancestral` | DPM++ 2S Ancestral |
| `k_dpmpp_sde` | DPM++ SDE |
| `ddim` | DDIM |

可选噪声调度：

| 调度 ID | 说明 |
|---------|------|
| `karras` | Karras（默认，V4.5/V5 推荐） |
| `exponential` | Exponential |
| `polyexponential` | Polyexponential |
| `native` | Native |

### 角色外观与画风

- `generation.character_prompt`：描述机器人自己的外观，适合自拍或画自己。
- `generation.style_reference`：固定画风标签，会自动加入提示词。
- `generation.negative_prompt`：所有图片共用的负面提示词。

### 图片保存位置

默认保存在：

```text
data/image_generator_plugin-neo/
```

其中：

- `temp_images/`：AI 自动调用和 WebUI 生成的图片。
- `command_images/`：聊天命令生成的图片。
- `vibes/`：Vibe 和精密参考素材。

## 聊天命令

所有命令需要管理员权限。

### 画图

```text
/画图 [画幅] <提示词> [|| 负面提示词] [--model 模型ID] [--steps N] [--scale X] [--rescale X] [--variety-plus true|false] [--render-text]
```

也可以使用 `/生图` 或 `/nai_image`。

示例：

```text
/画图 竖图 1girl, blue hair, outdoor
/画图 横图 fantasy city --model nai-diffusion-5-full --steps 24 --scale 6
/画图 方图 girl holding a sign "欢迎" --model nai-diffusion-5-curated --render-text
```

### 改图

先引用一张图片，再发送：

```text
/改图 <提示词> [强度] [--model 模型ID] [--steps N] [--variety-plus true|false] [--render-text]
```

也可以使用 `/修图` 或 `/nai_edit`。

强度越高，结果与原图差异越大；不填写时使用配置中的默认强度。

### 精密参考图

先引用一张图片，再发送：

```text
/参考图 <提示词> [--model V4.5模型ID] [--type 角色|风格|两者] [--fidelity X] [--strength X]
```

也可以使用 `/nai_ref`。

精密参考与图生图不同：它不会直接重绘原图，而是把参考图中的人物特征或画风用于生成一张新图片。
精密参考仅支持 V4.5。白名单含 V4.5 时命令会自动选择其中一个，也可以通过 `--model` 明确指定。

### Vibe 素材

```text
/风格 列表
/风格 添加 <文件名>
/风格 状态
/风格 清空
```

也可以使用 `/nai_vibe`。

Vibe 素材放入：

```text
data/image_generator_plugin-neo/vibes/
```

支持常见图片，以及 `.naiv4vibe`、`.naiv4vibebundle` 等 NovelAI Vibe 文件。手动加载的 Vibe 按用户或聊天流分别保存，不会与其他用户共用。

NovelAI 图片 API 不提供账号余额查询，因此“账号”命令只会返回不支持说明。

## AI 自动绘图

插件启用 Action 后，AI 可以根据对话主动调用绘图能力。

支持的自动能力包括：

- 普通文生图。
- 多人物和人物位置控制。
- 可选 Vibe。
- 可选精密参考图。
- 局部重绘。
- 去杂物。
- 背景移除。
- 线稿与草图转换。
- 线稿上色。
- 表情调整。

可以在配置中分别关闭不希望 AI 自动使用的能力。

## Vibe 配置

Vibe 有两种使用方式：

- `always`：每次生图都使用，适合固定画风。
- `selectable`：由 AI 根据名称和描述选择，适合按场景切换画风。

优先使用已经编码好的 `.naiv4vibe` 文件，可以避免重复调用编码接口。图片素材会在插件加载时编码。

## 精密参考配置

精密参考图可以预先放入 `director_reference.selectable`。每项可以设置：

- 名称和用途描述。
- `character`：主要参考人物。
- `style`：主要参考画风。
- `character&style`：同时参考人物与画风。
- 参考强度和忠实度。

该能力仅面向 NovelAI V4.5 模型。V5 暂不支持 Director Reference。

## PNG 元数据处理

插件可以在发送图片前剥离 NovelAI 写入的参数信息，本地保存的原图不会被改动。

相关配置：

- `generation.strip_metadata_command`
- `generation.strip_metadata_action`

开启后会清除 PNG 文本信息，并重写 Alpha 通道中可能携带的数据：

- 完全透明和完全不透明的像素保持不变。
- 半透明像素的透明度会量化为 16 级。
- 一般可以保留透明背景和半透明边缘，但极细微的透明度可能发生变化。

## WebUI

开启：

```toml
[webui]
enabled = true
route_path = "/plugins/image-generator"
```

然后访问主程序对应地址即可。

WebUI 可以：

- 测试文生图。
- 调整模型、画幅、采样参数和提示词。
- 编辑 Vibe、精密参考和提示词预设。
- 保存后立即让聊天侧使用新配置。

WebUI 不要求额外密码，也不会把 NovelAI Token 返回给浏览器。

## 常见问题

### 命令没有反应

确认插件已启用，并且发送者具有管理员权限。

### 提示词应该使用中文还是英文

取决于本次选择的模型。V4.5 使用英文标签式提示词；V5 可用英文 Tag 建立主体，再以简体中文、繁体中文、日文或英文自然语言描述复杂动作、关系和画面文字。

### 出现 429

插件会把图片任务放入串行队列，并按配置的冷却时间依次处理。频繁重试仍可能受到 NovelAI 上游限流。

### Vibe 文件找不到

命令中只填写素材文件名，不要填写完整路径。插件只会读取 `vibes/` 目录内的文件。

### 背景移除为什么消耗较多

NovelAI 的背景移除属于 Director 工具，通常会消耗 Anlas，具体费用以上游实际返回为准。

### Gateway 能生成普通图片，但某些编辑功能失败

确认 Gateway 版本支持对应的 OpenAI 图片扩展端点。普通文生图、图生图、局部重绘、Vibe 和 Director 工具使用不同接口。

## 费用提醒

- 标准画幅和常规步数对符合条件的 Opus 用户通常免费。
- 大图、高步数、人物参考、Vibe 编码、背景移除和图片放大可能消耗 Anlas。
- Gateway 的费用守卫和 NovelAI 最终扣费可能存在差异，批量或高费用操作前应自行确认。

## 更新与排障

修改配置后可以通过 WebUI 保存，或重载插件使配置重新生效。

若遇到问题，优先检查：

1. Token 是否有效。
2. `api.channel` 与 `api.base_url` 是否匹配。
3. Gateway 是否支持对应图片端点。
4. Vibe 文件是否位于正确目录。
5. 日志中 NovelAI 或 Gateway 返回的具体错误。
