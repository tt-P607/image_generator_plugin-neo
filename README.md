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

`available_models` 是 Bot 和 WebUI 可在单次调用中选择的严格白名单，非空时必须包含默认 `model`。留空则只允许默认模型。插件依据官方原生与 OpenAI 兼容对接规范提供集中式模型能力矩阵：

| 模型代际 | 包含模型 | 噪声调度 (noise_schedule) | Variety+ | Vibe | 角色参考 | 普通角色 | 步数上限 | 官方原生版本 |
|---|---|---|---|---|---|---|---|---|
| **V5** | `nai-diffusion-5-full`<br>`nai-diffusion-5-curated` | **不可选择，wire 固定 `karras`** | **不支持（彻底省略）** | 不支持 | 不支持 | 最多 32 人（自由坐标） | 28 步 | `params_version: 4` |
| **V4.5** | `nai-diffusion-4-5-full`<br>`nai-diffusion-4-5-curated` | 支持（按 sampler 矩阵校验） | 支持 | 支持 | 支持 | 最多 6 人（5×5 网格） | 50 步 | `params_version: 4` |
| **V4** | `nai-diffusion-4-full`<br>`nai-diffusion-4-curated-preview` | 支持（按 sampler 矩阵校验） | 支持 | 支持 | 不支持 | 最多 6 人（5×5 网格） | 50 步 | `params_version: 4` |
| **V3** | `nai-diffusion-3`<br>`nai-diffusion-furry-3` | 支持（按 sampler 矩阵校验） | 支持 | 支持 | 不支持 | 不支持 | 50 步 | `params_version: 4` |

Full 更适合精细控制，Curated 更偏稳定和审美一致。局部重绘会自动映射到同代 Inpainting 模型，不能把 `*-inpainting` 直接写入白名单。
注意：当用户配置了全局 `noise_schedule` 并在不同模型间切换时，插件会保留本地偏好，
在序列化最终请求时依据当前模型能力处理：使用 V5 时固定发送 `karras`，切换回
V4.5/V4/V3 时按 sampler 矩阵校验并恢复可用偏好。V4/V4.5/V5 的旧 `ddim`
配置会改写为 `k_euler_ancestral`；V4/V4.5 会依据改写前的 sampler 省略调度，
V5 仍固定发送 `karras`。V3 保留 `ddim` 并省略调度。

### 第三方中转的模型名

使用模型名与官方不同的第三方中转时，无需手动映射：只要模型名包含版本关键词，插件会自动推断能力档案——`4.5` / `4-5` / `45` 判为 V4.5，`5` / `v5` 判为 V5；名称含 `curated` 判为 Curated，否则按 Full。例如 `some-proxy/novelai-v5` 会按 V5 Full 处理，`proxy-4.5-curated` 会按 V4.5 Curated 处理，请求体仍发送原始模型名。

仅当模型名完全不含版本关键词、或自动推断不符合预期时，才需要 `model_aliases` 显式映射：

```toml
[generation]
model = "my-v5"
available_models = ["my-v5", "my-v45"]
model_aliases = { my-v5 = "nai-diffusion-5-full", my-v45 = "nai-diffusion-4-5-full" }
```

别名可写入 `model` 与 `available_models`，不能与官方模型 ID 重名；能力判断（提示词规则、多角色上限、Vibe 兼容性等）按映射的官方模型执行。

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

噪声调度会同时受模型代际和采样器限制：V3 常规采样器支持 `native`、`karras`、
`exponential`、`polyexponential`；V4/V4.5 常规采样器不支持 `native`；`k_dpm_2`
只支持 `exponential` 与 `polyexponential`；`k_dpm_2_ancestral` 是合法 sampler，
但在 V3/V4/V4.5 下没有可选调度，因此不发送该字段。V5 隐藏该选择项，最终请求
固定发送 `karras`。

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
/画图 竖图 1girl, blue hair --seed 0 --count 3
```

`--count` 允许 `1~4`。插件始终逐张串行请求，Gateway 每次请求的 `n` 固定为 `1`；
显式指定 seed 时，后续图片依次使用 `seed + 1`。

### 改图

先引用一张图片，再发送：

```text
/改图 <提示词> [强度] [--model 模型ID] [--steps N] [--variety-plus true|false] [--render-text]
```

也可以使用 `/修图` 或 `/nai_edit`。

强度越高，结果与原图差异越大；不填写时使用配置中的默认强度。

### Enhance 与固定 2× 放大

先引用一张图片，再执行普通 Enhance 或 V5 Max Enhance：

```text
/nai_edit enhance <完整提示词> [--model 模型ID] [--upscale 1x|1.5x|2x|Max] [--strength 0.5] [--noise 0] [--seed 0]
```

普通 Enhance 使用图生图管线，默认 Strength 为 `0.5`、Noise 为 `0`，并按源图面积
开放 `1x`、`1.5x`、`2x`。`Max` 仅 V5 可用，会发送 `upscaled_enhance: true`；
V4.5 只支持普通 Enhance。固定 2× Upscale 是独立的纯放大能力，不接受提示词、
Strength 或 Noise，且源图面积不能超过 `1,048,576`。

### 精密参考图

先引用一张图片，再发送：

```text
/参考图 <提示词> [--model V4.5模型ID] [--type 角色|风格|两者] [--fidelity X] [--strength X]
```

也可以使用 `/nai_ref`。

精密参考与图生图不同：它不会直接重绘原图，而是把参考图中的人物特征或画风用于生成一张新图片。
精密参考仅支持 V4.5，也可用于 V4.5 局部重绘。参考图会保持比例放入最接近原图
比例的 `1024x1536`、`1536x1024` 或 `1472x1472` 黑底画布。白名单含 V4.5 时
命令会自动选择其中一个，也可以通过 `--model` 明确指定。

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
- 普通 Enhance 与 V5 Max Enhance。
- 固定 2× 放大。
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
- 串行生成 1~4 张图片，并支持显式 seed。
- 上传图片执行普通 Enhance 或 V5 Max Enhance。
- 调整模型、画幅、采样参数和提示词。
- 编辑 Vibe、精密参考和提示词预设。
- 保存后立即让聊天侧使用新配置。

WebUI 不要求额外密码，也不会把 NovelAI Token 返回给浏览器。

## 常见问题

### 命令没有反应

确认插件已启用，并且发送者具有管理员权限。

### 提示词应该使用中文还是英文

取决于本次选择的模型。V4.5 使用英文标签式提示词；V5 可用英文 Tag 建立主体，再以简体中文、繁体中文、日文或英文自然语言描述复杂动作、关系和画面文字。

### 出现 HTTP 400 错误排查指南

HTTP 400 属于客户端参数错误，网关或官方会在请求校验不通过时直接拒绝。常见原因与排查方法：

1. **`noise_schedule` 校验失败**：V5 最终只允许 `karras`；旧模型按 sampler 能力矩阵校验。`k_dpm_2_ancestral` 不发送该字段，V4/V4.5 的旧 `ddim` 会改写 sampler 后省略该字段。
2. **`steps` 步数越界**：V5 模型步数必须在 1~28 之间；其他模型在 1~50 之间。如果传入大于 28 的步数给 V5，会被严格拒绝。
3. **分辨率或面积越界**：宽和高每边必须在 64~2048 之间，且必须按 64 对齐。V5 模型总像素不可超过 `1,048,576`（即 $1024 \times 1024$ 面积）。
4. **模型能力不匹配的开关**：向 V5 模型发送了 Variety+（`variety_boost` / `skip_cfg_above_sigma`）、Vibe 或角色参考图。使用 V5 时插件会自动过滤这些不支持的参数。
5. **角色数量超限**：V4/V4.5 最多 6 人，V5 最多 32 人，V3 不支持多角色。
6. **未识别的模型名**：第三方模型名未包含版本关键词（如 4.5/5）且未配置 `model_aliases`。

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
