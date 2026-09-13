# 图片 API 请求说明

本文档记录 `image_generator_plugin-neo` 实际发送的图片请求。插件支持直连 NovelAI 官方 API，以及参数对齐的 NovelAI Gateway。

配置文件：

```text
config/plugins/image_generator_plugin-neo/config.toml
```

## 模型选择与能力矩阵

插件提供集中式模型能力矩阵（依据《官方原生接口对接文档》与《OpenAI兼容接口对接文档》）：

| 模型代际 | 官方 ID 示例 | 噪声调度 (noise_schedule) | Variety+ | Vibe / 参考图 | 普通角色上限 | 步数上限 |
|---|---|---|---|---|---|---|
| **V5** | `nai-diffusion-5-full`<br>`nai-diffusion-5-curated` | **不可选择，wire 固定 `karras`** | **不支持（彻底省略）** | 不支持 | 最多 32 人（自由坐标） | 28 步 |
| **V4.5** | `nai-diffusion-4-5-full`<br>`nai-diffusion-4-5-curated` | 按 sampler 能力矩阵校验 | 支持 | 支持 | 最多 6 人（5×5 网格） | 50 步 |
| **V4** | `nai-diffusion-4-full`<br>`nai-diffusion-4-curated-preview` | 按 sampler 能力矩阵校验 | 支持 | 仅支持 Vibe | 最多 6 人（5×5 网格） | 50 步 |
| **V3** | `nai-diffusion-3`<br>`nai-diffusion-furry-3` | 按 sampler 能力矩阵校验 | 支持 | 仅支持 Vibe | 0（不支持多角色） | 50 步 |

单次请求的 `model`、`steps`、`scale`、`cfg_rescale` 和 `variety_plus` 会覆盖配置默认值。
注意：V5 模型在官方原生与 OpenAI 兼容格式下，最终 JSON 都会包含
`"noise_schedule": "karras"`。UI 不开放调度选择；配置中的旧偏好只保留在本地，
序列化时统一归一为 `karras`。V5 发送其他调度值会被网关严格返回 HTTP 400。
所有现代生图、图生图和局部重绘请求使用 `params_version: 4`。V4/V4.5/V5 的
旧 `ddim` 配置会改写为 `k_euler_ancestral`；V4/V4.5 按原始 sampler 省略
`noise_schedule`，V5 仍固定发送 `karras`。`k_dpm_2_ancestral` 是合法 sampler，
但 V3/V4/V4.5 不为它发送 `noise_schedule`。

## 渠道选择

```toml
[api]
channel = "official" # 或 gateway
api_keys = ["pst-xxxxxxxx"]
```

### official

```toml
base_url = "https://image.novelai.net/ai/generate-image"
api_base_url = "https://api.novelai.net"
```

### gateway

```toml
base_url = "http://127.0.0.1:8000"
```

Gateway 参数和端点以 `NOVELAI_API_DOC.md` 与 `API_REQUEST_DOC.md` 为依据。本插件只使用自身绘图功能需要的接口。

## 认证

所有请求均使用 NovelAI Token：

```http
Authorization: Bearer pst-xxxxxxxx
Content-Type: application/json
```

## official 请求

### 文生图 (Official 渠道 - V5 模型)

```http
POST https://image.novelai.net/ai/generate-image
Accept: application/zip
```

```json
{
  "input": "1girl, blue hair, outdoor",
  "model": "nai-diffusion-5-curated",
  "action": "generate",
  "parameters": {
    "params_version": 4,
    "width": 832,
    "height": 1216,
    "steps": 28,
    "scale": 5.0,
    "sampler": "k_euler_ancestral",
    "seed": 123456789,
    "n_samples": 1,
    "noise_schedule": "karras",
    "cfg_rescale": 0.0,
    "qualityToggle": true,
    "ucPreset": 0,
    "negative_prompt": "lowres, bad quality",
    "characterPrompts": [],
    "v4_prompt": {
      "caption": {
        "base_caption": "1girl, blue hair, outdoor",
        "char_captions": []
      },
      "use_coords": false,
      "use_order": true
    },
    "v4_negative_prompt": {
      "caption": {
        "base_caption": "lowres, bad quality",
        "char_captions": []
      },
      "legacy_uc": false
    }
  }
}
```

> **注意**：V5 模型的 `parameters` 中固定发送 `noise_schedule: "karras"`，彻底省略 `skip_cfg_above_sigma` 与 Vibe/参考图字段，`params_version` 为 4。
> V3、V4、V4.5 模型的请求同样使用 `params_version: 4`，并按 sampler 能力矩阵发送合法 `noise_schedule`。

V4、V4.5 与 V5 发送结构化提示词字段；V3 使用传统提示词字段。

### 多人物

多人物只用于 V4/V5 系列模型，需要同步填写三组字段：

```json
{
  "parameters": {
    "use_coords": true,
    "characterPrompts": [
      {
        "prompt": "1girl, red hair",
        "uc": "bad hands",
        "center": {"x": 0.3, "y": 0.5},
        "enabled": true
      }
    ],
    "v4_prompt": {
      "caption": {
        "base_caption": "2girls, outdoor",
        "char_captions": [
          {
            "char_caption": "1girl, red hair",
            "centers": [{"x": 0.3, "y": 0.5}]
          }
        ]
      },
      "use_coords": true,
      "use_order": true
    },
    "v4_negative_prompt": {
      "caption": {
        "base_caption": "lowres",
        "char_captions": [
          {
            "char_caption": "bad hands",
            "centers": [{"x": 0.3, "y": 0.5}]
          }
        ]
      },
      "legacy_uc": false
    }
  }
}
```

### 图生图

文生图 payload 改为：

```json
{
  "action": "img2img",
  "parameters": {
    "image": "<base64>",
    "strength": 0.7,
    "noise": 0.0,
    "extra_noise_seed": 123456789,
    "img2img": {
      "color_correct": true,
      "strength": 0.7
    },
    "add_original_image": true,
    "inpaintImg2ImgStrength": 0.7
  }
}
```

`generation.img2img_auto_downscale` 开启时，official 渠道会把超过一百万像素的源图等比缩小并对齐到 64 像素。

### 局部重绘

```json
{
  "input": "完整画面描述",
  "model": "nai-diffusion-5-full-inpainting",
  "action": "infill",
  "parameters": {
    "image": "<base64 source>",
    "mask": "<base64 rgba mask>",
    "strength": 0.7,
    "noise": 0,
    "img2img": {
      "color_correct": true,
      "strength": 1.0
    },
    "inpaintImg2ImgStrength": 0.7,
    "add_original_image": true
  }
}
```

遮罩为与目标图片同尺寸的 RGBA PNG：白色区域重绘，黑色区域保留，Alpha 固定为 255。
V5 Full 与 Curated 局部重绘都映射为 `nai-diffusion-5-full-inpainting`。
V4.5 局部重绘可携带与文生图相同的 Director Reference 字段。

### Vibe 编码

```http
POST https://image.novelai.net/ai/encode-vibe
```

```json
{
  "image": "<base64>",
  "information_extracted": 1.0,
  "model": "nai-diffusion-4-5-curated"
}
```

官方返回二进制编码数据，插件将其 Base64 编码后放入生成 payload：

```json
{
  "reference_image_multiple": ["<encoded vibe>"],
  "reference_strength_multiple": [0.6],
  "reference_information_extracted_multiple": [1.0]
}
```

### 精密参考

图片会保持比例并居中放入最接近源图比例的黑底 PNG 画布，候选画布为
`1024x1536`、`1536x1024` 与 `1472x1472`。Official 与 Gateway 共用该编码结果。

```json
{
  "director_reference_images": ["<base64>"],
  "director_reference_descriptions": [
    {
      "caption": {
        "base_caption": "character&style",
        "char_captions": []
      },
      "legacy_uc": false
    }
  ],
  "director_reference_strength_values": [1.0],
  "director_reference_secondary_strength_values": [0.0],
  "director_reference_information_extracted": [1.0]
}
```

### Director 工具

```http
POST https://image.novelai.net/ai/augment-image
```

```json
{
  "req_type": "declutter",
  "image": "<base64>",
  "width": 1024,
  "height": 1024
}
```

`req_type` 可为：

- `declutter`
- `bg-removal`
- `lineart`
- `sketch`
- `colorize`
- `emotion`

`colorize` 和 `emotion` 可额外发送 `prompt` 与 `defry`。

### Enhance

Official 的普通 Enhance 仍调用 `/ai/generate-image` 并使用 `action: "img2img"`。
以 `832x1216` 的 1.5× 为例，`parameters.width/height` 为 `1280/1856`，
Strength 默认为 `0.5`、Noise 默认为 `0`，提示词追加
`, -2::upscaled, blurry::,`。V5 Max Enhance 使用相同管线，但目标宽高取源图
nearest-64，并额外发送：

```json
{
  "parameters": {
    "upscaled_enhance": true
  }
}
```

Max 仅 V5 可用；V4.5 只支持普通 Enhance。

## Gateway 请求

新版网关（v0.4.0+）统一使用 ``POST /v1/images/generations`` 端点，根据请求体字段
和 ``extra`` 字段自动路由到对应功能。不再使用独立的 img2img / inpainting /
vibe-transfer / encode-vibe / upscale / director-* 端点。

### 路由规则

| 请求体特征 | 网关行为 |
|-----------|---------|
| `prompt` | 文生图 |
| `prompt` + `image` | 图生图 |
| `prompt` + `image` + `mask` | 局部重绘 |
| `extra: "upscale"` + `image` | 固定 2× 放大 |
| `extra: "encode-vibe"` + `image` | Vibe 编码 |
| `extra: "director-{tool}"` + `image` | 导演工具 |
| `params.reference_image_multiple` 非空 | Vibe 风格转移（文生图附带参考图） |

普通 Enhance 不使用 `extra`，而是以图生图请求发送目标尺寸、Strength、Noise 和
增强后的提示词。Max Enhance 同样走图生图，并在 `params` 中附加
`"upscaled_enhance": true`。它们都与 `extra: "upscale"` 的固定 2× 放大分离。

### 文生图 (Gateway 渠道 - V5 模型)

```http
POST /v1/images/generations
Content-Type: application/json
```

```json
{
  "model": "nai-diffusion-5-curated",
  "prompt": "1girl, blue hair, outdoor",
  "n": 1,
  "size": "832x1216",
  "params": {
    "steps": 28,
    "scale": 5.0,
    "cfg_rescale": 0.0,
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "negative_prompt": "lowres, bad quality",
    "quality": true,
    "uc_preset": "strong"
  }
}
```

> **注意**：
> 1. V5 模型的 `params` 中固定发送 `"noise_schedule": "karras"`，且彻底省略 `variety_boost`。
> 2. V4/V4.5/V3 模型按 sampler 能力矩阵发送合法 `noise_schedule`；`k_dpm_2_ancestral` 不发送该字段。V4/V4.5 的旧 `ddim` 先迁移 sampler，再按原始 sampler 省略该字段。
> 3. 每个 Gateway wire 请求的 `n` 固定为 `1`。请求 2~4 张图片时，插件队列逐次发送独立请求；显式 seed 按次序递增。

### 多人物

```json
{
  "params": {
    "characters": [
      {
        "prompt": "1girl, red hair",
        "uc": "bad hands",
        "center": {"x": 0.3, "y": 0.5},
        "enabled": true
      }
    ],
    "use_coords": true
  }
}
```

### 精密参考

```json
{
  "params": {
    "character_references": [
      {
        "image": "<base64>",
        "type": "character&style",
        "strength": 1.0,
        "fidelity": 1.0,
        "information_extracted": 1.0
      }
    ]
  }
}
```

### 图生图

```http
POST /v1/images/generations
```

在文生图基础上额外提供 `image` 字段即走图生图：

```json
{
  "model": "nai-diffusion-5-curated",
  "prompt": "1girl, blue dress",
  "image": "<base64>",
  "strength": 0.7,
  "size": "1024x1024",
  "params": {
    "scale": 5.0,
    "cfg_rescale": 0.0,
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "negative_prompt": "lowres"
  }
}
```

### 局部重绘

```http
POST /v1/images/generations
```

在图生图基础上额外提供 `mask` 字段即走局部重绘：

```json
{
  "model": "nai-diffusion-5-curated",
  "prompt": "完整画面描述",
  "image": "<base64 source>",
  "mask": "<base64 mask>",
  "strength": 0.7,
  "size": "1024x1024",
  "params": {
    "scale": 5.0,
    "cfg_rescale": 0.0,
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "negative_prompt": "lowres"
  }
}
```

### Vibe 风格转移

```http
POST /v1/images/generations
```

在文生图基础上的 `params` 中附带 `reference_image_multiple` 字段即走 Vibe 转移：

```json
{
  "model": "nai-diffusion-4-5-curated",
  "prompt": "portrait of a girl",
  "size": "832x1216",
  "params": {
    "reference_image_multiple": ["<encoded vibe>"],
    "reference_strength_multiple": [0.6],
    "reference_information_extracted_multiple": [1.0]
  }
}
```

### Vibe 编码

```http
POST /v1/images/generations
```

通过 `extra: "encode-vibe"` 触发：

```json
{
  "model": "nai-diffusion-4-5-curated",
  "extra": "encode-vibe",
  "image": "<base64>",
  "information_extracted": 1.0
}
```

响应（统一端点返回 list 格式）：

```json
{
  "data": [{"b64_json": "<encoded vibe>"}]
}
```

### 2× 放大

```http
POST /v1/images/generations
```

通过 `extra: "upscale"` 触发：

```json
{
  "model": "nai-diffusion-5-curated",
  "extra": "upscale",
  "image": "<base64>",
  "response_format": "b64_json"
}
```

Gateway 根据源图尺寸执行固定 2× 放大；`width`、`height` 和自定义倍率不参与选择。
插件在发送前要求源图面积不超过 `1,048,576`。

### Enhance

普通 Enhance 通过标准图生图请求发送，默认 Strength 为 `0.5`、Noise 为 `0`。
目标面积不超过 `3,145,728`，wire 宽高按最近的 64 像素对齐；提示词追加
`, -2::upscaled, blurry::,`。示例仅展示区别字段：

```json
{
  "model": "nai-diffusion-4-5-full",
  "prompt": "1girl, -2::upscaled, blurry::,",
  "image": "<base64>",
  "strength": 0.5,
  "noise": 0,
  "size": "1280x1856",
  "n": 1
}
```

Max Enhance 仅 V5 可用，要求源图面积严格小于 `2,516,582.4`，保持接近源图的
nearest-64 尺寸，并发送：

```json
{
  "params": {
    "upscaled_enhance": true
  }
}
```

### 导演工具

```http
POST /v1/images/generations
```

通过 `extra: "director-{tool}"` 触发：

| extra 值 | 功能 |
|---------|------|
| `director-declutter` | 去杂物 |
| `director-bg-remover` | 精细抠图 |
| `director-lineart` | 提取线稿 |
| `director-sketch` | 转铅笔画 |
| `director-colorize` | 线稿上色 |
| `director-emotion` | 改变表情 |

请求示例：

```json
{
  "model": "nai-diffusion-5-curated",
  "extra": "director-colorize",
  "image": "<base64>",
  "width": 1024,
  "height": 1024,
  "prompt": "bright orange and blue",
  "defry": 1,
  "response_format": "b64_json"
}
```

## Gateway 响应

插件兼容两种 OpenAI 图片响应：

### Base64

```json
{
  "data": [
    {"b64_json": "<base64>"}
  ]
}
```

### URL

```json
{
  "data": [
    {"url": "https://gateway.example/images/result.png"}
  ]
}
```

每个请求只接收并保存一张图片。多图任务由插件串行发送多个 `n: 1` 请求。
Gateway 本身负责生成结果的 URL 可访问性和认证行为。

## 常用参数

| 参数 | 范围或值 | 说明 |
|---|---|---|
| `steps` | `1–50` | 采样步数 |
| `scale` | `1.0–10.0` | 提示词引导强度 |
| `sampler` | 见下表 | 采样器 |
| `noise_schedule` | 见下表 | 噪声调度 |
| `cfg_rescale` | `0.0–1.0` | CFG 缩放 |
| `ucPreset` | `0–4` | UC 预设 |
| `strength` | `0.01–1.0` | 图生图或重绘强度 |
| `fidelity` | `0.0–1.0` | 精密参考忠实度 |
| `defry` | `0–5` | 上色或表情工具去噪参数 |
| `type` | `character` / `style` / `character&style` | 精密参考类型 |
| `n` | 固定 `1` | Gateway 单次请求数量；多图由插件串行拆分 |

### 采样器

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

### 噪声调度

| 调度 ID | 说明 |
|---------|------|
| `karras` | Karras（默认，V4/V5 推荐） |
| `exponential` | Exponential |
| `polyexponential` | Polyexponential |
| `native` | Native（仅 V3 模型使用，插件对 V3 模型自动切换为此调度） |

`k_dpm_2_ancestral` 在 V3/V4/V4.5 下没有可选调度。V5 不论 sampler 都固定发送
`karras`。V3 的 `ddim` 保留且不发送调度；V4/V4.5/V5 的旧 `ddim` 会迁移为
`k_euler_ancestral`。

## 响应与错误

- official 生图、重绘和 Director 通常返回 ZIP，插件读取其中第一张图片。
- official Vibe 编码返回二进制数据。
- 普通图片、mask、Vibe 与 Precise Reference 输入仅接受可解码的 PNG/JPEG/WebP；损坏或 MIME 与实际格式不一致的 data URL 会在排队前拒绝。
- Gateway 返回 OpenAI 图片 JSON。
- 429 会按插件队列和重试策略处理。
- 其他 HTTP 错误会将上游错误摘要返回给调用方并写入日志。
