# 图片 API 请求说明

本文档记录 `image_generator_plugin-neo` 实际发送的图片请求。插件支持直连 NovelAI 官方 API，以及参数对齐的 NovelAI Gateway。

配置文件：

```text
config/plugins/image_generator_plugin-neo/config.toml
```

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

### 文生图

```http
POST https://image.novelai.net/ai/generate-image
Accept: application/zip
```

```json
{
  "input": "1girl, blue hair, outdoor",
  "model": "nai-diffusion-4-5-curated",
  "action": "generate",
  "parameters": {
    "params_version": 3,
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
    },
    "characterPrompts": [],
    "reference_image_multiple": [],
    "reference_strength_multiple": [],
    "reference_information_extracted_multiple": []
  }
}
```

V3 模型不发送 `v4_prompt`、`v4_negative_prompt` 和其他 V4 专用字段。

### 多人物

多人物只用于 V4 系列模型，需要同步填写三组字段：

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
  "model": "nai-diffusion-4-5-curated-inpainting",
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

## Gateway 请求

### 文生图

```http
POST /v1/images/generations
```

```json
{
  "model": "nai-diffusion-4-5-curated",
  "prompt": "1girl, blue hair, outdoor",
  "negative_prompt": "lowres, bad quality",
  "size": "832x1216",
  "n": 1,
  "steps": 28,
  "scale": 5.0,
  "cfg_rescale": 0.0,
  "sampler": "k_euler_ancestral",
  "noise_schedule": "karras",
  "ucPreset": 0,
  "quality": true,
  "variety_boost": false,
  "use_coords": false,
  "response_format": "b64_json"
}
```

### Gateway 多人物

```json
{
  "characters": [
    {
      "prompt": "1girl, red hair",
      "negative_prompt": "bad hands",
      "position": [0.3, 0.5],
      "enabled": true
    }
  ],
  "use_coords": true
}
```

### Gateway 精密参考

```json
{
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
```

### Gateway 图生图

```http
POST /v1/images/img2img
```

```json
{
  "model": "nai-diffusion-4-5-curated",
  "prompt": "1girl, blue dress",
  "image": "<base64>",
  "strength": 0.7,
  "size": "1024x1024",
  "scale": 5.0,
  "cfg_rescale": 0.0,
  "sampler": "k_euler_ancestral",
  "noise_schedule": "karras",
  "negative_prompt": "lowres",
  "response_format": "b64_json"
}
```

### Gateway 局部重绘

```http
POST /v1/images/inpainting
```

```json
{
  "model": "nai-diffusion-4-5-curated",
  "prompt": "完整画面描述",
  "image": "<base64 source>",
  "mask": "<base64 mask>",
  "strength": 0.7,
  "size": "1024x1024",
  "scale": 5.0,
  "cfg_rescale": 0.0,
  "sampler": "k_euler_ancestral",
  "noise_schedule": "karras",
  "negative_prompt": "lowres",
  "response_format": "b64_json"
}
```

### Gateway Vibe 编码

```http
POST /v1/images/encode-vibe
```

```json
{
  "model": "nai-diffusion-4-5-curated",
  "image": "<base64>",
  "information_extracted": 1.0
}
```

响应：

```json
{
  "data": "<encoded vibe>"
}
```

### Gateway Vibe Transfer

```http
POST /v1/images/vibe-transfer
```

```json
{
  "model": "nai-diffusion-4-5-curated",
  "prompt": "portrait of a girl",
  "reference_image_multiple": ["<encoded vibe>"],
  "reference_strength_multiple": [0.6],
  "reference_information_extracted_multiple": [1.0],
  "width": 832,
  "height": 1216,
  "scale": 5.0,
  "cfg_rescale": 0.0,
  "response_format": "b64_json"
}
```

### Gateway Director

| 功能 | 端点 |
|---|---|
| 去杂物 | `/v1/images/director-declutter` |
| 背景移除 | `/v1/images/director-bg-remover` |
| 线稿 | `/v1/images/director-lineart` |
| 草图 | `/v1/images/director-sketch` |
| 上色 | `/v1/images/director-colorize` |
| 表情 | `/v1/images/director-emotion` |

请求示例：

```json
{
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

插件保存第一张图片。Gateway 本身负责生成结果的 URL 可访问性和认证行为。

## 常用参数

| 参数 | 范围或值 | 说明 |
|---|---|---|
| `steps` | `1–50` | 采样步数 |
| `scale` | `1.0–10.0` | 提示词引导强度 |
| `cfg_rescale` | `0.0–1.0` | CFG 缩放 |
| `ucPreset` | `0–4` | UC 预设 |
| `strength` | `0.01–1.0` | 图生图或重绘强度 |
| `fidelity` | `0.0–1.0` | 精密参考忠实度 |
| `defry` | `0–5` | 上色或表情工具去噪参数 |
| `type` | `character` / `style` / `character&style` | 精密参考类型 |

## 响应与错误

- official 生图、重绘和 Director 通常返回 ZIP，插件读取其中第一张图片。
- official Vibe 编码返回二进制数据。
- Gateway 返回 OpenAI 图片 JSON。
- 429 会按插件队列和重试策略处理。
- 其他 HTTP 错误会将上游错误摘要返回给调用方并写入日志。
