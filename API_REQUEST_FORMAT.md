# NovelAI 图片生成 API 请求格式文档

> 本文档基于 NovelAI 官方 API 逆向整理，结合第三方 SDK [caru-ini/novelai-sdk](https://github.com/caru-ini/novelai-sdk) 的实现，
> 记录本插件使用的完整请求格式。适用模型：V4（nai-diffusion-4-*）、V3（nai-diffusion-3）。
>
> 本插件支持两种生图渠道，通过 `api.channel` 配置项切换：
> - **official**（默认）：直连 NovelAI 官方 API，支持全部功能（Vibe Transfer、Director Reference、多人物等）。
> - **gateway**：通过 novelai-gateway 中转分发渠道，使用 OpenAI Chat Completions 兼容接口，参数受限但部署更灵活。

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
9. [Gateway 渠道（OpenAI 兼容接口）](#9-gateway-渠道openai-兼容接口)
   - [9.1 渠道概述与限制](#91-渠道概述与限制)
   - [9.2 Chat Completions 接口](#92-chat-completions-接口)
   - [9.3 消息格式与多人物](#93-消息格式与多人物)
   - [9.4 响应处理](#94-响应处理)
   - [9.5 配置示例](#95-配置示例)

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

### 多人物模式 (Multi-Character)

V4 模型支持在同一张图片中指定多个不同特征的人物及其位置。
若要在请求中启用多人物模式，需同步设置以下字段（缺一不可）：
1. 开启全局 `use_coords` 和 `v4_prompt.use_coords` 为 `true`。
2. 填写 `characterPrompts` 数组定义角色。
3. 同步填充 `v4_prompt.caption.char_captions`（正面提示词关联）和 `v4_negative_prompt.caption.char_captions`（负面提示词关联）。

**参数结构示意：**
```json
{
  "parameters": {
    "use_coords": true,
    "v4_prompt": {
      "use_coords": true,
      "caption": {
        "base_caption": "<全局正面提示词>",
        "char_captions": [
          {
            "char_caption": "<人物A正面提示词>",
            "centers": [{"x": 0.25, "y": 0.5}]
          },
          {
            "char_caption": "<人物B正面提示词>",
            "centers": [{"x": 0.75, "y": 0.5}]
          }
        ]
      }
    },
    "v4_negative_prompt": {
      "caption": {
        "base_caption": "<全局负面提示词>",
        "char_captions": [
          {
            "char_caption": "<人物A负面提示词>",
            "centers": [{"x": 0.25, "y": 0.5}]
          },
          {
            "char_caption": "<人物B负面提示词>",
            "centers": [{"x": 0.75, "y": 0.5}]
          }
        ]
      }
    },
    "characterPrompts": [
      {
        "prompt": "<人物A正面提示词>",
        "uc": "<人物A负面提示词>",
        "center": {"x": 0.25, "y": 0.5},
        "enabled": true
      },
      {
        "prompt": "<人物B正面提示词>",
        "uc": "<人物B负面提示词>",
        "center": {"x": 0.75, "y": 0.5},
        "enabled": true
      }
    ]
  }
}
```
*注：`x` 和 `y` 为相对于图片宽高的坐标比例 (0.0 - 1.0)。*

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
| `scale` | float | 1.0–10.0 | CFG 引导比例（Prompt Guidance）。越高越贴合提示词，但过高会过饱和；官网默认 **6.5**，插件默认 **5.0** |
| `steps` | int | 1–50 | 采样步数，步数越多细节越丰富，但耗时增加；推荐 **28** |
| `sampler` | string | 见下表 | 采样器，决定生图的随机性和风格倾向；官网推荐 **k_euler_ancestral** |
| `noise_schedule` | string | 见下表 | 噪声调度，配合采样器使用；V4 推荐 **karras**，V3 固定 **native** |
| `seed` | int | 0–999999999 | 随机种子，相同种子+相同参数可复现图像（但换采样器/模型仍会变动） |
| `n_samples` | int | 1–8 | 批量生成数量（插件固定 1） |
| `cfg_rescale` | float | 0.0–1.0 | 提示词引导重新缩放（V4 专用）。调高可修正过高 scale 导致的过饱和问题；官网默认 **0.0**，可配置（`prompt_guidance_rescale`） |
| `skip_cfg_above_sigma` | float \| null | `19.0` 或 `null` | **Variety+**：`null` = 关闭（插件默认），`19.0` = 开启（官网默认）。开启后细节多样性提升，但提示词贴合度略降 |
| `qualityToggle` | bool | true/false | 服务端自动追加官方质量词（插件固定 `true`） |
| `ucPreset` | int | 0–3 | 负面词预设等级（插件固定 `0` = 轻度） |
| `params_version` | int | 3 | V4 模型固定传 `3` |
| `prefer_brownian` | bool | true/false | V4 中使用布朗运动调度，推荐 `true` |
| `sm` / `sm_dyn` / `autoSmea` | bool | false | V4 中已失效（inop），固定 `false` |

---

### 采样器（sampler）

| API 值 | 官网显示名 | 说明 | 推荐场景 |
|--------|-----------|------|---------|
| `k_euler_ancestral` | **Euler Ancestral**（官网推荐） | 每步引入随机噪声，多样性最强，画面活泼 | 人物、创意图，官网默认 |
| `k_euler` | Euler | 确定性采样，风格稳定，同 seed 完全可复现 | 追求一致性/批量对比 |
| `k_dpmpp_2s_ancestral` | DPM++ 2S Ancestral | 质量高、细节丰富，带随机性，耗时较长 | 高质量精出图 |
| `k_dpmpp_2m` | DPM++ 2M | 高质量确定性采样 | 质量+稳定性均衡 |
| `k_dpmpp_2m_sde` | DPM++ 2M SDE | 带 SDE 扩散的 2M，细节更多 | 高细节出图 |
| `k_dpmpp_sde` | DPM++ SDE | 细节最丰富，耗时最长 | 细节控 |

> **插件默认**：`k_euler_ancestral`（与官网一致）

---

### 噪声调度（noise_schedule）

| API 值 | 官网显示名 | 说明 |
|--------|-----------|------|
| `karras` | **karras**（官网推荐） | 非线性降噪，整体质量好，V4 首选 |
| `exponential` | exponential | 指数降噪，风格偏向柔和 |
| `polyexponential` | polyexponential | 多项式指数，细节更平滑 |
| `native` | — | V3 模型默认/唯一选项，V4 不推荐 |

> **插件行为**：V4 模型使用 config 中的 `noise_schedule`（默认 `karras`）；V3 模型强制使用 `native` 忽略配置。

---

### Variety+（skip_cfg_above_sigma）

| 状态 | API 值 | 效果 |
|------|--------|------|
| 关闭 | `null` | 全程引导，提示词贴合度高，同提示词出图差异小 |
| 开启 | `19.0` | 主体成形前跳过引导，增加细节多样性，同提示词出图差异大 |

> **插件配置**：`generation.variety_plus = true/false`（默认 `false`）  
> 官网默认开启。三张图差异过大时，可尝试关闭 Variety+ 并固定 seed。

---

### scale 与 cfg_rescale 联动说明

| scale 范围 | 建议 cfg_rescale | 效果描述 |
|-----------|----------------|---------|
| 5.0–6.5 | 0.0–0.3 | 官方推荐区间，画面自然 |
| 6.5–8.0 | 0.3–0.7 | 高引导，颜色更饱和，cfg_rescale 避免过饱和 |
| 8.0 以上 | 0.5–1.0 | 极高引导，非常贴合提示词，但容易过饱和失真 |

> 插件命令支持实时覆盖：`/画图 1girl --scale 6.5 --rescale 0.3`

---

### 插件 config 与 API 参数对应关系

| config 字段 | API 参数 | 备注 |
|-------------|----------|------|
| `generation.scale` | `parameters.scale` | 直接映射，命令可用 `--scale` 覆盖 |
| `generation.steps` | `parameters.steps` | 直接映射 |
| `generation.sampler` | `parameters.sampler` | 直接映射，默认 `k_euler_ancestral` |
| `generation.noise_schedule` | `parameters.noise_schedule` | V3 强制 `native` |
| `generation.prompt_guidance_rescale` | `parameters.cfg_rescale` | V4 专用，命令可用 `--rescale` 覆盖 |
| `generation.variety_plus` | `parameters.skip_cfg_above_sigma` | `true` → `19.0`，`false` → `null` |
| `generation.negative_prompt` | `v4_negative_prompt.caption.base_caption` | 与 AI 额外负面词合并后注入 |
| `generation.resolution` | `parameters.width/height` | 命令未指定画幅时的兜底 |
| `vibe.presets[].ie` | `reference_information_extracted_multiple[]` | 编码时和注入时都使用 |
| `vibe.presets[].strength` | `reference_strength_multiple[]` | 注入时使用 |

---

## 9. Gateway 渠道（OpenAI 兼容接口）

> 对应配置：`api.channel = "gateway"`，`api.base_url` 填写 gateway 服务地址

### 9.1 渠道概述与限制

novelai-gateway 是一个将 NovelAI 图像生成能力包装为 OpenAI Chat Completions 兼容接口的中转服务。
插件在 `channel = "gateway"` 时使用此渠道，认证方式与官方渠道相同（`pst-*` Key）。

**与官方渠道的差异：**

| 功能 | official | gateway |
|------|----------|---------|
| Vibe Transfer | ✅ 支持 | ❌ 不支持 |
| Director Reference | ✅ 支持 | ❌ 不支持 |
| 图生图（img2img） | ✅ 支持 | ❌ 不支持 |
| 多人物坐标 | ✅ 支持 | ✅ 支持（通过 system 消息） |
| 负面提示词 | ✅ 支持 | ✅ 支持（通过 system 消息） |
| scale / cfg_rescale | ✅ 支持 | ✅ 支持 |
| 画幅 | 任意 64 倍数 | 仅 832×1216 / 1024×1024 / 1216×832 |
| 步数 | 可配置 | 固定 28（网关锁定） |
| 响应格式 | ZIP/PNG 二进制 | Markdown 图片链接 |

### 9.2 Chat Completions 接口

**端点**：`POST {gateway_base_url}/v1/chat/completions`

**请求体**：

```json
{
  "model": "nai-diffusion-4-5-curated",
  "stream": false,
  "scale": 5.0,
  "cfg_rescale": 0.7,
  "sampler": "k_euler_ancestral",
  "noise_schedule": "karras",
  "width": 832,
  "height": 1216,
  "messages": [
    {
      "role": "user",
      "content": "1girl, blue hair, outdoor, best quality"
    },
    {
      "role": "system",
      "content": "Negative prompt: lowres, bad quality, blurry"
    },
    {
      "role": "system",
      "content": "Characters: [{\"prompt\": \"1girl, red hair\", \"uc\": \"bad hands\", \"center\": {\"x\": 0.3, \"y\": 0.5}}]"
    }
  ]
}
```

**顶层参数说明**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `model` | string | 是 | — | 模型名称，见支持的模型列表 |
| `stream` | bool | 否 | `false` | 插件固定传 `false` |
| `scale` | float | 否 | `5.0` | 提示词引导强度，范围 `1.0–10.0` |
| `cfg_rescale` | float | 否 | `0.7` | CFG 缩放比例，范围 `0.0–1.0` |
| `width` | int | 否 | `832` | 图片宽度，只允许 `832`、`1024`、`1216` |
| `height` | int | 否 | `1216` | 图片高度，只允许 `832`、`1024`、`1216` |
| `sampler` | string | 否 | `k_euler_ancestral` | 采样器 |
| `noise_schedule` | string | 否 | `karras` | 噪声调度 |

**支持的模型**：

| 模型 ID | 说明 |
|---------|------|
| `nai-diffusion-4-5-curated` | V4.5 精选版（默认） |
| `nai-diffusion-4-5-full` | V4.5 完整版 |
| `nai-diffusion-4-curated-preview` | V4 精选预览版 |
| `nai-diffusion-3` | V3 |
| `nai-diffusion-furry-3` | V3 Furry |

### 9.3 消息格式与多人物

**正面提示词**（`role: "user"`）：

```json
{"role": "user", "content": "1girl, blue hair, outdoor, best quality"}
```

**负面提示词**（`role: "system"`，`Negative prompt:` 前缀）：

```json
{"role": "system", "content": "Negative prompt: lowres, bad quality, blurry"}
```

插件会将全局负面词（`generation.negative_prompt`）与 Action 传入的额外负面词合并后，
通过此格式传给 Gateway。

**多人物坐标**（`role: "system"`，`Characters:` 前缀）：

```json
{
  "role": "system",
  "content": "Characters: [{\"prompt\": \"1girl, red hair\", \"uc\": \"bad hands\", \"center\": {\"x\": 0.3, \"y\": 0.5}}, {\"prompt\": \"1girl, blue hair\", \"uc\": \"bad anatomy\", \"center\": {\"x\": 0.7, \"y\": 0.5}}]"
}
```

- `center.x` / `center.y`：相对坐标（`0.0–1.0`），`{x: 0.5, y: 0.5}` 为画面正中央。
- 插件从 `draw_image` Action 的 `characters` 参数解析后自动构造此消息。

### 9.4 响应处理

Gateway 返回标准 OpenAI Chat Completions 格式，`content` 字段为 Markdown 图片链接：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "![image](https://your-gateway.example.com/images/abc123.png)"
      }
    }
  ]
}
```

插件通过正则提取 URL，然后下载图片并保存到本地（与官方渠道保存路径相同）。

### 9.5 配置示例

```toml
[api]
# 切换到 Gateway 渠道（只改这两项即可）
channel = "gateway"

# 填写 Gateway 服务颁发的 API Key
api_keys = ["your-gateway-api-key"]

# base_url 改为 gateway 服务地址，支持含 /v1 后缀，插件会自动规范化
# 示例（本地部署）：http://127.0.0.1:31555
# 示例（远程中转）：https://your-gateway.example.com/v1
base_url = "https://your-gateway.example.com/v1"

proxy = ""
cooldown = 3

[generation]
model = "nai-diffusion-4-5-curated"
scale = 5.0
cfg_rescale = 0.7
sampler = "k_euler_ancestral"
noise_schedule = "karras"
```

> **注意**：切换到 `gateway` 渠道后，Vibe Transfer 和 Director Reference 功能将自动跳过，
> 不会报错，但也不会生效。如需使用这些功能，请切回 `official` 渠道。
