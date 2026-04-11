# NovelAI 图片生成 API 请求格式文档

> 本文档基于 NovelAI 官方 API 逆向整理，结合第三方 SDK [caru-ini/novelai-sdk](https://github.com/caru-ini/novelai-sdk) 的实现，
> 记录本插件使用的完整请求格式。适用模型：V4（nai-diffusion-4-*）、V3（nai-diffusion-3）。

---

## 目录

1. [端点列表](#1-端点列表)
2. [认证](#2-认证)
3. [文生图请求（V4 模型）](#3-文生图请求v4-模型)
4. [文生图请求（V3 模型）](#4-文生图请求v3-模型)
5. [图生图请求](#5-图生图请求)
6. [Vibe Transfer 流程](#6-vibe-transfer-流程)
   - [6.1 编码端点](#61-编码端点encode-vibe)
   - [6.2 在生图请求中注入 Vibe](#62-在生图请求中注入-vibe)
7. [响应处理](#7-响应处理)
8. [参数速查表](#8-参数速查表)

---

## 1. 端点列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `https://image.novelai.net/ai/generate-image` | POST | 文生图 / 图生图 |
| `https://image.novelai.net/ai/encode-vibe` | POST | Vibe 图片编码（生图前必须调用） |

> Base URL 可通过 `config/plugins/image_generator_plugin/config.toml` 的 `api.base_url` 字段覆盖。

---

## 2. 认证

所有请求需在 Header 中携带：

```http
Authorization: Bearer pst-xxxxxxxxxxxxxxxxxx
Content-Type: application/json
Accept: application/zip
Origin: https://novelai.net
Referer: https://novelai.net
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...
```

---

## 3. 文生图请求（V4 模型）

适用：`nai-diffusion-4-curated`、`nai-diffusion-4-full`、`nai-diffusion-4-5-curated`、`nai-diffusion-4-5-full`。

### 请求体

```json
{
  "input": "<正面提示词>",
  "model": "nai-diffusion-4-5-curated",
  "action": "generate",
  "parameters": {
    "width": 1024,
    "height": 1024,
    "scale": 6.5,
    "steps": 28,
    "sampler": "k_euler",
    "seed": 123456789,
    "n_samples": 1,
    "ucPreset": 0,
    "qualityToggle": true,
    "sm": false,
    "sm_dyn": false,
    "autoSmea": false,
    "noise_schedule": "karras",

    "params_version": 3,
    "cfg_rescale": 0.7,
    "legacy": false,
    "legacy_v3_extend": false,
    "legacy_uc": false,
    "add_original_image": true,
    "controlnet_strength": 1,
    "dynamic_thresholding": false,
    "prefer_brownian": true,
    "normalize_reference_strength_multiple": true,
    "use_coords": true,
    "inpaintImg2ImgStrength": 1,
    "deliberate_euler_ancestral_bug": false,
    "skip_cfg_above_sigma": null,

    "negative_prompt": "<负面提示词>",

    "v4_prompt": {
      "caption": {
        "base_caption": "<正面提示词>",
        "char_captions": []
      },
      "use_coords": true,
      "use_order": true
    },
    "v4_negative_prompt": {
      "caption": {
        "base_caption": "<负面提示词>",
        "char_captions": []
      },
      "legacy_uc": false
    },

    "characterPrompts": [],

    "reference_image_multiple": [],
    "reference_information_extracted_multiple": [],
    "reference_strength_multiple": []
  }
}
```

### 关键参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `scale` | float | CFG 引导比例，建议 5.0–7.0 |
| `cfg_rescale` | float 0–1 | 提示词引导重新缩放，建议 0.0–0.7；配置项 `prompt_guidance_rescale` |
| `steps` | int 1–50 | 采样步数，28 是较好的平衡 |
| `sampler` | string | 采样器，V4 常用 `k_euler`、`k_dpmpp_2s_ancestral` |
| `noise_schedule` | string | V4 用 `karras`；V3 用 `native` |
| `qualityToggle` | bool | `true` 时服务端会自动追加官方质量词 |
| `seed` | int 0–999999999 | 随机种子，超出范围行为未定义 |
| `params_version` | int | V4 模型固定传 `3` |
| `sm` / `sm_dyn` | bool | V4 中已失效（inop），传 `false` |
| `autoSmea` | bool | V4 中已失效（inop），传 `false` |

### 画幅

官方 API 的 `width` 和 `height` 理论上支持 64–1600 之间任意 64 的倍数，但官方推荐（也是计费优化点）的标准尺寸如下：

| 名称 | 宽 × 高 | 适用场景 |
|------|---------|---------|
| 方图 | 1024 × 1024 | 通用 |
| 横图 | 1216 × 832 | 风景、横版场景 |
| 竖图 | 832 × 1216 | 人物、竖版场景 |

> **本插件限制**：为简化使用，目前 `_parse_resolution` 只接受以上三种尺寸，传入其他值将回退到配置的默认画幅。如需支持自定义尺寸，可直接修改 `valid_sizes` 集合。

### 加权语法

NovelAI V4 采用 `n::tag::` 语法：

```
1.3::red hair::       # 提升权重到 1.3×
0.8::background::     # 降低权重到 0.8×
masterpiece, best quality, 1girl, 1.2::detailed eyes::
```

---

## 4. 文生图请求（V3 模型）

适用：`nai-diffusion-3`、`nai-diffusion-3-inpainting`。

```json
{
  "input": "<正面提示词>",
  "model": "nai-diffusion-3",
  "action": "generate",
  "parameters": {
    "width": 1024,
    "height": 1024,
    "scale": 5.0,
    "steps": 28,
    "sampler": "k_euler",
    "seed": 123456789,
    "n_samples": 1,
    "ucPreset": 0,
    "qualityToggle": true,
    "sm": false,
    "sm_dyn": false,
    "noise_schedule": "native",
    "negative_prompt": "<负面提示词>"
  }
}
```

V3 不需要 `v4_prompt`、`params_version`、`cfg_rescale` 等 V4 专有字段。

---

## 5. 图生图请求

在文生图请求基础上，修改 `action` 并在 `parameters` 中追加：

```json
{
  "action": "img2img",
  "parameters": {
    "image": "<原图 base64>",
    "strength": 0.7,
    "noise": 0.0
  }
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `image` | string (base64) | 原始图片，PNG 格式推荐 |
| `strength` | float 0–1 | 修改强度，越高偏离原图越多（0.0 = 完全保留） |
| `noise` | float | 额外噪声，通常传 `0.0` |

---

## 6. Vibe Transfer 流程

> **重要**：NovelAI API 不接受原始图片作为 Vibe 输入，必须先通过 `/ai/encode-vibe` 端点编码。

### 6.1 编码端点（encode-vibe）

**端点**：`POST https://image.novelai.net/ai/encode-vibe`

**请求体**：

```json
{
  "image": "<原始图片 base64，PNG/JPG>",
  "information_extracted": 0.7,
  "model": "nai-diffusion-4-5-curated"
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `image` | string (base64) | 原始图片（PNG 或 JPG） |
| `information_extracted` | float 0–1 | 信息提取量，0 = 只学风格，1 = 学全部内容 |
| `model` | string | 必须与后续生图使用的模型一致 |

**响应**：返回裸字节数据（不是 JSON），需手动 `base64.b64encode(response.content)` 转成字符串。

```python
encoded_vibe = base64.b64encode(await resp.read()).decode("utf-8")
```

### 6.2 在生图请求中注入 Vibe

将编码后的数据放入 `parameters`：

```json
{
  "reference_image_multiple": [
    "<encoded_vibe_base64>",
    "<encoded_vibe2_base64>"
  ],
  "reference_information_extracted_multiple": [0.7, 1.0],
  "reference_strength_multiple": [0.6, 0.5],
  "normalize_reference_strength_multiple": true
}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `reference_image_multiple` | list[string] | encode-vibe 返回的编码数据列表（非原始图片！） |
| `reference_information_extracted_multiple` | list[float] | 与编码时对应的 `information_extracted` 值 |
| `reference_strength_multiple` | list[float] | Vibe 参考强度，越高生成结果越贴近参考风格 |
| `normalize_reference_strength_multiple` | bool | 多 Vibe 时建议传 `true`，归一化参考强度 |

##### `.naiv4vibe` 文件格式

```json
{
  "identifier": "novelai-vibe-transfer",
  "version": 1,
  "type": "image",
  "image": "<原始图片 base64（PNG）>"
}
```

使用时：读取 `image` 字段 → 调 `encode-vibe` → 用返回值注入生图请求。

**不可**直接将 `.naiv4vibe` 文件中的 `image` 字段传入 `reference_image_multiple`。

---

## 7. 响应处理

### 成功响应

| 情况 | 说明 |
|------|------|
| 状态码 200/201 | 成功 |
| Content-Type: application/zip | 多数情况下返回 ZIP 包，内含一张 PNG |
| 直接 PNG | 某些情况下直接返回 PNG 字节 |

**ZIP 解压处理**：

```python
import io, zipfile

with zipfile.ZipFile(io.BytesIO(response_bytes)) as zf:
    for name in zf.namelist():
        if name.lower().endswith((".png", ".jpg")):
            img_data = zf.read(name)
            break
```

**格式判断**：

```python
if content[:4] == b"PK\x03\x04":  # ZIP magic
    # 走 ZIP 解压流程
elif content[:4] == b"\x89PNG":   # PNG magic
    # 直接使用
```

### 错误响应

| 状态码 | 含义 |
|--------|------|
| 400 | 请求参数错误 |
| 401 | API Key 无效或已过期 |
| 402 | Anlas 积分不足 |
| 409 | 冲突（如并发请求过多） |
| 429 | 请求过于频繁，触发限流 |
| 5xx | 服务器内部错误 |

错误响应体为 JSON：`{"statusCode": 401, "message": "…"}`

---

## 8. 参数速查表

### 生图参数（parameters）

| 参数名 | 类型 | 范围/示例 | 说明 |
|--------|------|-----------|------|
| `width` | int | 64–1600（64 的倍数） | 图片宽度，官方推荐 832/1024/1216 |
| `height` | int | 64–1600（64 的倍数） | 图片高度，官方推荐 832/1024/1216 |
| `scale` | float | 0–10 | CFG 引导比例 |
| `steps` | int | 1–50 | 采样步数 |
| `sampler` | string | 见下 | 采样器 |
| `seed` | int | 0–999999999 | 随机种子 |
| `n_samples` | int | 1–8 | 批量生成数量 |
| `noise_schedule` | string | karras/native | 噪声调度 |
| `cfg_rescale` | float | 0–1 | 提示词引导重缩放（V4 专用） |
| `params_version` | int | 3 | V4 模型固定值 |
| `qualityToggle` | bool | true/false | 是否追加官方质量词 |
| `ucPreset` | int | 0–3 | 负面词预设等级（0 = 轻度） |
| `negative_prompt` | string | - | 负面提示词 |

### 采样器（sampler）

| 值 | 说明 |
|----|------|
| `k_euler` | 快速稳定，推荐 |
| `k_euler_ancestral` | 多样性更强 |
| `k_dpmpp_2s_ancestral` | 质量高，较慢 |
| `k_dpmpp_2m` | 质量高，快速 |
| `k_dpmpp_sde` | 细节丰富 |

### 插件 config 与 API 参数对应关系

| config 字段 | API 参数 | 备注 |
|-------------|----------|------|
| `generation.scale` | `parameters.scale` | 直接映射 |
| `generation.steps` | `parameters.steps` | 直接映射 |
| `generation.sampler` | `parameters.sampler` | 直接映射 |
| `generation.noise_schedule` | `parameters.noise_schedule` | V3 强制 `native` |
| `generation.prompt_guidance_rescale` | `parameters.cfg_rescale` | V4 专用 |
| `generation.negative_prompt` | `v4_negative_prompt.caption.base_caption` | 与 AI 提供的额外负面词合并后注入 |
| `generation.resolution` | `parameters.width/height` | LLM 未指定或无效时的兜底 |
| `vibe.presets[].ie` | `reference_information_extracted_multiple[]` | 编码时和注入时都使用 |
| `vibe.presets[].strength` | `reference_strength_multiple[]` | 注入时使用 |
