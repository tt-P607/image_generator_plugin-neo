# 衣柜系统优化讨论

## 现状分析

当前实现是"纯 LLM 自动生成"方案：每天调用 LLM，传入角色人设 + 季节/日期类型，生成三时段服装 tags，然后软注入到 DrawAction 描述里。

### 现有方案的问题

1. **LLM 生成不稳定**：tags 质量取决于 LLM 对 NAI 语法的熟悉程度，每次生成结果随机，风格可能不一致
2. **没有记忆**：LLM 不知道上次穿了什么，可能天天生成相似的衣服
3. **无法精确控制**：用户无法精确指定"这件衣服"，只能依赖 LLM 的理解
4. **软注入效果有限**：仅追加到 action description，Chatter 可能不会严格遵守

---

## 最优解设计思路

### 核心矛盾

| 维度 | 纯 LLM 生成 | 纯手动管理 |
|------|------------|-----------|
| 灵活性 | 高（自动感知季节/场合） | 低（需要手动设置每套） |
| 准确性 | 低（生成不稳定） | 高（精确控制 tags） |
| 维护成本 | 低 | 高（需要提前配置衣柜） |
| 自然感 | 高（每天不同） | 一般（固定轮换） |

### 推荐方案：混合模式（手动衣柜 + LLM 选择）

**核心思路**：
- 用户手动维护一个"衣柜"，定义若干套完整服装（含 tags）
- 每天由 LLM 根据日期/季节/场合，从衣柜中**选择**最合适的一套
- LLM 只做"选择"，不做"创作"，避免生成随机 tags 的质量问题

#### 数据结构

```json
{
  "wardrobe": {
    "outfits": [
      {
        "id": "summer_casual",
        "name": "夏日休闲",
        "tags": "white tank top, denim shorts, sneakers, hair ribbon",
        "seasons": ["summer"],
        "occasions": ["weekend", "holiday"],
        "segments": ["daytime", "evening"]
      },
      {
        "id": "jk_uniform",
        "name": "JK 制服",
        "tags": "1.2::school uniform::, white blouse, navy pleated skirt, black thigh-highs, loafers",
        "seasons": ["spring", "autumn", "winter"],
        "occasions": ["workday"],
        "segments": ["daytime"]
      },
      {
        "id": "pajamas_pink",
        "name": "粉色睡衣",
        "tags": "pink pajamas, oversized shirt, bare feet, hair down",
        "seasons": ["all"],
        "occasions": ["all"],
        "segments": ["night"]
      }
    ]
  }
}
```

#### 工作流程

1. 用户提前在 JSON 或 config 中定义衣柜（精确 tags）
2. 每天启动时，把衣柜列表 + 今天的背景信息传给 LLM
3. LLM 返回每个时段选择哪套衣服（返回 `outfit_id`）
4. 系统根据 id 取出对应 tags，注入 DrawAction

#### 优势

- **精度高**：tags 由用户自己写，LLM 不会乱造
- **灵活**：LLM 仍然有判断场景的能力（选择哪套）
- **可扩展**：可以支持手动覆盖（/nai_wardrobe wear jk_uniform）
- **记忆友好**：每天的选择结果可以记录，避免重复

---

### 轻量改进方案（对现有方案的优化）

如果不想重构，当前方案可做以下改进来提升质量：

#### 1. Few-shot 示例注入

在 system prompt 里加入 2-3 个高质量示例输出：

```
示例输出（仅供格式参考，不要复制内容）：
{
  "daytime": "1.2::school uniform::, white blouse, navy pleated skirt, black thigh-highs, loafers, hair ribbon, 1.1::cute accessories::",
  "evening": "oversized hoodie, short shorts, bare legs, socks, casual wear",
  "night": "pink pajamas, oversized shirt, hair down, bare feet, soft fabric"
}
```

#### 2. 角色特征明确传入

在 user message 里明确列出角色关键外貌，让 LLM 在选服装时考虑搭配：

```
角色关键特征：粉色长发、精灵尖耳、163cm、少女感甜美风格
→ LLM 会倾向生成与这些特征搭配的服装（不会生成运动短裤+球鞋这类风格冲突的搭配）
```

#### 3. 输出格式约束增强

要求 LLM 必须按固定格式输出，并加入"思考过程"要求（CoT），让 LLM 先想场合再列 tags：

```
请按以下格式输出：
[时段分析]
daytime: 工作日/周末 + 季节 → 场合类型 → 风格定位
→ [生成的 tags]
...
最终 JSON: {...}
```

#### 4. 限定词汇范围（可选）

提供一个推荐词汇表，要求 LLM 优先从中选取，减少生造 tags：

```
推荐服装词汇库：
- 上衣: white shirt, blouse, turtleneck, crop top, cardigan, hoodie, ...
- 下装: skirt, pleated skirt, mini skirt, shorts, jeans, ...
（已在当前 prompt 中实现）
```

---

## 结论与建议

| 场景 | 推荐方案 |
|------|---------|
| 想要精确可控的效果 | **混合模式**（手动衣柜 + LLM 选择） |
| 想要低维护成本的效果 | **轻量改进**（当前方案 + Few-shot + 角色特征） |
| 初期测试 | 先用当前方案测试生成质量，再决定是否重构 |

### 当前阶段建议

先测试当前实现，在 config 里 `enabled = true`，运行 `/nai_wardrobe refresh` 看看生成结果。  
如果生成的 tags 质量不满意（乱造、风格不搭、缺少层次），再升级到混合模式。

---

## 实现路线图（如果需要升级）

**阶段 1**（当前）：纯 LLM 生成  
**阶段 2**：轻量改进（Few-shot + 角色特征精确传入）  
**阶段 3**：混合模式（手动衣柜 JSON + LLM 选择）  
**阶段 4**：完整衣柜系统（Web UI 管理 + 换装命令 + 记忆功能）
