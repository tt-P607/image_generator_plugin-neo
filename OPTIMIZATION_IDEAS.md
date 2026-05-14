# Image Generator Plugin — 优化方向分析

> 基于当前代码质量审查和功能讨论，整理后续可优化的几个方向。

---

## 1. Action 架构：draw_image vs generate_selfie

### 现状问题

两个 Action 的底层执行路径完全一致，均调用 `generate_and_send_image → service.generate_image`。
`generate_selfie` 的实际差异只有：
- 参数拆成了 `scene_description + pose_or_action + mood` 三段
- 提示词构建时自动拼入 `character_prompt`

### 优化选项

**方案 A（推荐）：合并 + character 注入由 LLM 完成**

删除 `generate_selfie`，只保留 `draw_image`。
在 `draw_image` 的 `action_description` 中注入 character_prompt 的角色定义，并说明：

> "当画的是你自己时，必须在提示词中包含以下角色标签：{character_prompt}"

- 优：LLM 工具选择更简单，不存在"该用哪个"的歧义
- 缺：依赖 LLM 记住角色标签，可能遗漏
- 适合：对 LLM 质量有较高信心时

**方案 B：保留两个，但统一 selfie 参数接口**

将 `generate_selfie` 的 `scene_description + pose_or_action + mood` 合并成一个 `content_description`，和 `draw_image` 保持一致。
`character_prompt` 继续由服务端自动注入，LLM 无需知道。

- 优：保留语义分离，LLM 有明确的"画自己"专用入口；参数更简洁
- 缺：两个 action 仍然存在，仍需维护两份描述
- 适合：希望保留清晰的自拍专属路径时

**方案 C：保持现状**

适合：不想改动结构，当前分参数方式已能满足需求。

---

## 2. NSFW 灵活性

### 现状问题

LLM 完全不知道可以生成成人内容，因为：
1. `action_description` 没有任何 NSFW 相关引导
2. `nai-diffusion-4-5-curated` 是审查模型，本身有内容过滤倾向
3. 负面提示词中包含大量质量过滤词，但没有针对 NSFW 场景的切换逻辑

### 优化方案

#### 2.1 配置层

在 `generation` 配置段新增：

```toml
[generation]
nsfw_enabled = false
nsfw_model = "nai-diffusion-4-5"  # 切换到非审查模型
```

在 `prompt` 配置段新增：

```toml
[prompt]
nsfw_instructions = """
当用户明确请求成人/色情内容时，可在提示词中使用以下标签：
- 基础：explicit, nsfw, nude, topless 等
- NovelAI 专属：可在 content_description 中直接使用官方标签
负面提示词中不要加 nsfw_neg 或 lowres，仅保留解剖/质量过滤词
"""
```

#### 2.2 代码层

在 `on_plugin_loaded` 中：
- 若 `nsfw_enabled=True`，将 `nsfw_instructions` 注入两个 action 描述
- 可选：将 `service.model` 切换到 `nsfw_model` 指定的非审查版本

#### 2.3 uc_preset 调整

| 场景 | 建议 uc_preset | 说明 |
|------|---------------|------|
| 普通图像 | 0（Strong）| 最通用 |
| 正常人物 | 3（Human Focus）| 防止 Q 版化 |
| NSFW | 4（None）| 不追加任何 UC，由提示词完全控制 |

可在配置中保留 `uc_preset` 供用户手动调整，或新增 `nsfw_uc_preset` 覆盖。

---

## 3. 提示词质量

### 3.1 quality_tags 的问题

`draw_action._build_prompt` 当前固定拼入：

```
masterpiece, best quality, ultra detailed, official art, 1.3::very aesthetic::
```

NAI V4 模型已经不太依赖这类质量词（会有轻微提示词污染），可考虑：
- 将质量词抽成配置项 `generation.quality_prefix`，默认值可保留，用户可自定义
- NSFW 模式下可去掉 `official art`（与 NSFW 内容语义冲突）

### 3.2 negative_prompt 分层

当前负面提示词是全局单一字符串，无法按场景切换。可考虑：

```toml
[generation]
negative_prompt_base = "bad anatomy, extra fingers..."    # 基础解剖过滤（始终使用）
negative_prompt_quality = "worst quality, jpeg artifacts..."   # 质量过滤（可选）
negative_prompt_nsfw_off = "nsfw, nude, explicit..."    # SFW 模式追加
```

---

## 4. 其他小优化

### 4.1 队列超时

当前任务队列没有超时机制，某个生图任务卡死会阻塞后续所有请求。
建议给 `_enqueue_task` 增加超时参数（如 120s），超时后设置异常结果。

### 4.2 API Key 轮换策略

当前是顺序轮换，可考虑：
- 记录每个 key 的上次 429 时间，优先选择冷却时间已到的 key
- 多 key 场景下并行发送

### 4.3 command_user 用户 ID

命令场景下 `user_id` 固定为 `"command_user"`，导致所有命令用户共享同一个 Vibe 缓存。
可从 `self._message` 中提取真实 user_id（如果框架提供的话）。

---

## 优先级建议

| 优先级 | 优化项 | 复杂度 |
|--------|--------|--------|
| P0 | NSFW 配置开关 + 描述注入 | 低 |
| P1 | selfie 参数简化（方案 B）| 低 |
| P1 | quality_prefix 抽为配置 | 低 |
| P2 | negative_prompt 分层 | 中 |
| P2 | 队列超时机制 | 中 |
| P3 | API Key 智能轮换 | 高 |
