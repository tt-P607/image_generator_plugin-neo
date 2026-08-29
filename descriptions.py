"""画图 Action 描述文本构建。

画图能力的可用画风、预设、参考图及底层生图模型都来自用户配置，
在插件加载或配置热重载时自动检测当前生效模型，并动态组装专属提示词规范
注入到 Action description 和 System Reminder，让模型明确感知当前模型与专属语法。
"""

from __future__ import annotations

from pathlib import Path

from .config import ImageGeneratorConfig
from .engine.models import get_model_profile
from .engine.types import V3_MODELS, V4_MODELS, V5_MODELS


def detect_model_generation(model: str) -> str:
    """根据固定的确切模型名称判定 NovelAI 模型代际（绝对匹配）。

    Args:
        model: 精确模型名，如 'nai-diffusion-5-curated'

    Returns:
        'v5' | 'v4' | 'v3' | 'unknown'
    """
    cleaned = model.strip()
    if cleaned in V5_MODELS:
        return "v5"
    if cleaned in V4_MODELS:
        return "v4"
    if cleaned in V3_MODELS:
        return "v3"
    return "unknown"


def _model_header_block(config: ImageGeneratorConfig) -> str:
    """构建默认生图模型声明头。"""
    model = config.generation.model.strip() or "nai-diffusion-5-curated"
    generation = detect_model_generation(model)
    gen_title = {
        "v5": "NovelAI V5 架构",
        "v4": "NovelAI V4 / V4.5 架构",
        "v3": "NovelAI V3 架构",
    }.get(generation, "NovelAI 最新架构")

    return (
        f"【默认生图模型（不指定 model 参数时生效）】\n"
        f"  模型名称：`{model}`（{gen_title}）\n"
        f"  请按实际所用模型对应的专属语法与特性构建提示词。"
    )


def _base_structure_block() -> str:
    """构建不绑定模型代际的提示词结构说明。"""
    return (
        "**1. 基础结构与九段顺位**\n"
        "越靠前的内容越影响画面基调与构图。依次组织：画师/艺术媒介/品质 → 人数与性别 → "
        "角色身份与作品 → 身体容貌 → 服装配饰 → 动作表情与互动 → 环境背景 → "
        "光影/景深/视角 → 整体质感与复杂度。\n"
        "具体语言、自然语言用法、Token 上限和权重区间必须遵守所选模型的专属规则。"
    )


def _character_naming_block() -> str:
    """构建通用的 Danbooru 角色与皮肤命名规范。"""
    return (
        "**3. 角色与皮肤标签规范**\n"
        "  - 角色标准名：角色名 (作品名)，如 Castorice (honkai: star rail)\n"
        "  - 官方皮肤/形态：角色名_(皮肤名)_(作品名)，如 Hu_Tao_(Cherries_Snow-Laden)_(genshin_impact)\n"
        "  - 游戏 CG 原画风格：1.2::game_name (game cg)::\n"
        "  - ASCII 转写：角色名中的长音/变音符号必须转为标准英文字母，"
        "如 Gotō Hitori 写作 Gotoh Hitori (Bocchi the Rock!)\n"
        "  - 大小写不敏感，下划线与括号需按标准拼写"
    )


def _has_selectable_v4_model(config: ImageGeneratorConfig) -> bool:
    """判断可选模型列表中是否包含 V4 系列模型。

    Args:
        config: 已校验的插件配置实例

    Returns:
        可选列表存在且包含 V4 模型时返回 True；列表为空时回退判断默认模型
    """
    if config.generation.available_models:
        return any(
            detect_model_generation(m) == "v4"
            for m in config.generation.available_models
        )
    return detect_model_generation(config.generation.model) == "v4"


def _model_specific_prompt_block(model: str) -> str:
    """构建一个受支持模型的完整提示词与能力说明。"""

    profile = get_model_profile(model)
    edition = "Full" if profile.edition == "full" else "Curated"
    if profile.is_v5:
        return (
            f"**{model}：NovelAI V5 {edition} 专属规则**\n"
            "  - Prompt 上限 1471 Tokens。最佳写法是混合提示词：先用英文 Danbooru Tag "
            "确定画风、人数、角色、外貌、服装、环境和镜头，再用完整英语自然语言句子描述"
            "复杂动作、人物互动、肢体接触、视线与空间关系。V5 也理解日语及简繁中文描述，"
            "但不要套用 V4.5 的纯 Tag、纯英文短词限制。\n"
            "  - 知识库覆盖至 2026 年 7 月：新公开版权角色（如绝区零、鸣潮、星铁新角色）"
            "可直接用标准英文名调用，无需回避。\n"
            "  - 权重使用成对闭合的 `n::内容::`。主体与动作微调推荐 1.3~1.8，降权推荐"
            " 0.6~0.8，不要滥用 >2.0；混合画风时单项用 0.1~0.8，全部风格权重之和约为 1.0。"
            "数字结尾标签必须用空格、逗号或下划线隔离，避免权重解析错位。\n"
            "  - Full 与 Curated 母体差异较大：风格词从一个版本迁移到另一个版本出现劣化时，"
            "在 0.1~0.8 区间单独微调单项权重，不要暴力加权。\n"
            "  - 画面文字上限约 750 Tokens：加入 text、speech bubble 等载体词，"
            "用半角双引号直接包裹英文、日文或中文内容，例如 \"欢迎回家\"；日文也可用「」。"
            "多角色对话要用英语自然语言明确谁说话、气泡颜色、文字颜色及对应台词。\n"
            "  - 文字质量阶梯：英文与日文效果最好，简体/繁体中文良好可用，"
            "其他语言无法在画面内渲染文字。\n"
            "  - 原生 Alpha：透明背景用 transparent background；发光/粒子特效用 has alpha；"
            "半透明雨伞、薄纱等材质用 alpha transparency。原生透明无需调用付费 RemoveBG。\n"
            "  - 复杂度控制：low complexity 用于极简扁平，medium complexity 用于常规画面，"
            "high complexity 是日常精细插画首选，ultra complexity 用于机械或高密大场景；"
            "它控制单步细节预算，不能由增加 Steps 替代。\n"
            "  - 其他控制词：meta: novel era 偏复古、meta: golden era 偏现代黄金期、"
            "depthness 强化光影景深、attractive male 强化男性立体容貌。\n"
            "  - 视觉小说资产：visual novel art 为整体画风，visual novel bg 为纯背景，"
            "visual novel cg 为事件插画，visual novel sprite 为角色立绘，visual novel chibi 为 Q 版；"
            "透明立绘组合 visual novel sprite 与 transparent background。\n"
            "  - Meme 表情包：使用 xxx_(meme) 标签（如 padoru_(meme)），"
            "配合双引号台词与 transparent background 直出。\n"
            "  - 多格漫画：先写格式段（three-panel comic page, horizontal layout, "
            "clear panel borders, correct reading order, speech bubbles, no additional characters），"
            "再用 The first/second/third comic panel shows... 逐格写英语自然语言动作与引号台词。\n"
            "  - 多角色使用 characters 独立描述并自由定位。x/y 是 0.0~1.0 连续归一化坐标，"
            "可使用 0.17、0.43、0.86 等任意小数精确模拟官网自由拖动，不要吸附或取整到 5×5 格点。"
            "版权角色硬上限 22（官方），"
            "原创角色因一致性建议不超过 6；每个角色 prompt 可混合身份/外貌/服装 Tag 与英语自然语言动作。"
            "坐标不可完全重叠，否则容易发生肢体黏连。V5 不要求 V4.5 的 5×5 网格，也不要强制套用"
            " source#/target#/mutual# 互动标签，复杂互动直接写清施动者、受动者和空间关系。\n"
            "  - 参数边界：Steps 固定使用管理员配置，AI Action 不得覆盖。Guidance、PGR 与 Variety+ "
            "不增加生成消耗，但默认也不覆盖配置；仅在明确需要纠正提示词服从度、过曝或复杂动态构图时调整。"
            "Guidance 通常为 4.5~6.5，PGR 通常为 0；复杂动作或漫画才考虑开启 Variety+。\n"
            "  - 官网 UI 另有 Chunks 提示词收藏和 Enhance Max 放大，但它们不是 draw_image "
            "的可调用参数；不要虚构 chunks 或 enhance_max 字段。\n"
            "  - 不支持 Vibe 与 Director Reference；需要参考图时改选白名单中的 V4.5 模型。"
        )

    return (
        f"**{model}：NovelAI V4.5 {edition} 专属规则**\n"
        "  - Prompt 上限 505 Tokens，以英文 Danbooru Tag、半角逗号和 ASCII 字符为主；"
        "复杂修饰拆成独立标签，不使用 V5 的中日文自然语言工作流。\n"
        "  - 知识库覆盖至 2025 年 6 月；较新的版权角色可能无法仅凭角色名稳定还原，"
        "需要补充外貌、服装和标志性配饰 Tag。\n"
        "  - 冒号权重 `n::Tag::` 推荐区间为 1.1~1.4，降权推荐 0.6~0.9。"
        "双冒号必须成对闭合；数字结尾标签必须用空格、逗号或下划线隔离。\n"
        "  - 画面文字必须在主提示词中加入 text、english text、speech bubble 等标签，"
        "并在末尾空一行使用大写 `TEXT: 要显示的文字`；不要使用 V5 引号直出规则。\n"
        "  - 支持 Vibe（selected_vibes）与 Director Reference（selected_director_refs）。\n"
        "  - 多角色最多 6 个，使用 characters 的 x/y 坐标（对应官网 5×5 网格站位，"
        "如 B3 ≈ x 0.5 / y 0.5）；互动在角色提示词中成对使用"
        " source#/target#，对等互动使用 mutual#。\n"
        "  - 参数边界：Steps 固定使用管理员配置，AI Action 不得覆盖。Guidance、PGR 与 Variety+ "
        "默认不覆盖配置；只有明确需要时才调整。Guidance 通常为 4.5~6.0，PGR 通常为 0，"
        "复杂构图才考虑开启 Variety+。\n"
        "  - 不支持 V5 原生 Alpha、Control Tags、视觉小说控制词与 V5 漫画长文本工作流。"
    )


def _configured_generation_models(config: ImageGeneratorConfig) -> tuple[str, ...]:
    """按默认模型优先顺序返回 Bot 可选择的模型。"""

    configured = [config.generation.model, *config.generation.available_models]
    return tuple(dict.fromkeys(model.strip() for model in configured if model.strip()))


def _all_model_specific_blocks(config: ImageGeneratorConfig) -> str:
    """构建配置白名单内全部模型的专属能力说明。"""

    return "\n\n".join(
        _model_specific_prompt_block(model)
        for model in _configured_generation_models(config)
    )


def _multi_character_block(config: ImageGeneratorConfig) -> str:
    """构建多角色与互动说明。"""
    return (
        "**多角色参数格式**\n"
        "使用 characters 参数传入 JSON 数组，V4.5 官方上限 6 人，V5 版权角色官方上限 22 人；"
        "V5 原创角色为保证一致性建议不超过 6 人。每项字段：\n"
        "  - prompt：角色专属提示词（必填）。V4.5 使用英文 Tag；V5 推荐英文 Tag 与英语自然语言混合，"
        "写明身份、外貌、服装、动作、互动对象及视线\n"
        "  - uc：角色专属负面词（可省略）\n"
        "  - x：水平归一化坐标 0.0~1.0（0=最左，1=最右，默认 0.5）\n"
        "  - y：垂直归一化坐标 0.0~1.0（0=最上，1=最下，默认 0.5）\n"
        "  - V5：x/y 在范围内是连续自由坐标，可使用任意小数模拟官网拖动，不得量化为 5×5 网格\n"
        "  - V4.5：仍按官网 5×5 网格语义选择大致站位，再换算为对应 x/y\n"
        '示例：[{"prompt":"1girl, blonde hair, white dress","x":0.3,"y":0.5},'
        '{"prompt":"1girl, black hair, blue dress","x":0.7,"y":0.5}]\n\n'
        "**6. V4.5 角色互动语法（仅 V4.5）**\n"
        "  - 施动方：source#动作（如 source#hugging，填在发起角色的 prompt）\n"
        "  - 受动方：target#动作（如 target#hugging，填在接受角色的 prompt）\n"
        "  - 相互动作：mutual#动作（如 mutual#holding hands，双方 prompt 均填）\n"
        "  - 规则：source/target 成对出现在不同角色上；不要在 content_description 重复互动动作\n\n"
        "**V5 角色互动规则**\n"
        "  - 不沿用上述 V4.5 专属互动标签限制；直接在各角色 prompt 中用英语自然语言写清"
        "谁对谁做什么、肢体接触、朝向、视线与左右/前后关系\n"
        "  - 使用 0.0~1.0 连续自由坐标定位，角色中心不可完全重叠；角色较多时让 content_description "
        "明确整体构图、镜头和人数，避免模型自行增加人物\n\n"
        "**注意事项**\n"
        "  - 多角色时 content_description 只写环境/光影/构图与人数标签，角色细节放 characters\n"
        "  - ⚠️ 人数标签必须与 characters 精确对应：逐个统计各角色的性别后，把总人数写入 "
        "content_description（如两男一女必须写 2boys, 1girl；两女写 2girls）。"
        "漏写、多写或性别不匹配会导致画面人物数量错乱甚至崩坏\n"
        "  - 角色数量必须遵守实际所选模型的官方上限；单人物生图时不要传 characters，留空即可"
    )


def _composition_and_filename_block() -> str:
    """构建画幅与文件名规范。"""
    return (
        "**7. 构图画幅与文件命名**\n"
        "  - 画幅尺寸：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024\n"
        "  - 文件名规范（output_filename，必填）：\n"
        "    每次出图必须指定文件名，仅英文/数字/下划线，不含扩展名。\n"
        "    命名建议：内容描述_序号，如 character_portrait_01、landscape_sunset_02。\n"
        "    出图成功后返回值包含实际文件名，后续 inpaint_image / director_tool 可通过此文件名引用。"
    )


SKIP_STYLE_HINT = (
    "  ⚠️ 如果当前场景不适合此画风（如特殊形态、表情包、纯风景等），"
    "请在 content_description 中加入 `no_style` 来跳过画风注入。"
)
FORCED_STYLE_HINT = "  ⚠️ 画风标签为强制注入，不可跳过。"


def _style_block(config: ImageGeneratorConfig) -> str:
    """构建默认画风标签说明块。"""

    style = config.generation.style_reference.strip()
    if not style:
        return ""

    hint = SKIP_STYLE_HINT if config.generation.allow_skip_style else FORCED_STYLE_HINT
    return f"【默认画风标签（系统自动拼接到提示词最前面）】\n  {style}\n{hint}"


def _negative_block(config: ImageGeneratorConfig) -> str:
    """构建内置负面提示词说明块。"""

    negative = config.generation.negative_prompt.strip()
    if not negative:
        return ""
    return f"【已内置负面提示词（无需重复填写）】\n{negative}"


def _character_block(config: ImageGeneratorConfig) -> str:
    """构建角色外观说明块。"""

    character = config.generation.character_prompt.strip()
    if not character:
        return ""
    return f"【角色外观描述（画自己时参考）】\n{character}"


def _preset_block(config: ImageGeneratorConfig) -> str:
    """构建预设场景指令块。

    内容为空的预设只是内部触发机制，不展示给模型。
    """
    presets = [item for item in config.prompt.presets if item.content.strip()]
    if not presets:
        return ""

    lines = ["【预设场景指令】"]
    for preset in presets:
        trigger = f"（{preset.trigger}）" if preset.trigger else ""
        lines.append(f"  - {preset.name}{trigger}：{preset.content}")
    return "\n".join(lines)


def _custom_block(config: ImageGeneratorConfig) -> str:
    """构建用户自定义指引块。"""

    custom = config.prompt.custom_instructions.strip()
    if not custom:
        return ""
    return f"【自定义指引】\n{custom}"


def _model_list_block(config: ImageGeneratorConfig) -> str:
    """构建可选模型列表块。"""
    models = _configured_generation_models(config)
    default = config.generation.model.strip()
    lines = [
        "【可选模型列表（通过 model 参数指定，不指定则使用默认模型）】"
        "\n  只能从此白名单选择；每次调用必须按所选模型的专属规则构建其他参数。"
    ]
    for model in models:
        profile = get_model_profile(model)
        suffix = "（默认）" if model == default else ""
        lines.append(f"  - `{model}` [{profile.family} {profile.edition}]{suffix}")

    lines.append(
        "  先根据任务选择模型：V5 适合多语言、复杂叙事、文字、透明素材与漫画；"
        "V4.5 适合 Vibe、Director Reference 和稳定的纯 Tag 工作流。"
    )
    return "\n".join(lines)


def _vibe_block(config: ImageGeneratorConfig) -> str:
    """构建可选 Vibe 列表块。"""

    if not config.vibe.selectable_enabled or not config.vibe.selectable:
        return ""
    if not _has_selectable_v4_model(config):
        return ""

    lines = [
        "【可选 Vibe 画风列表（通过 selected_vibes 参数选择，可多选，逗号分隔）】"
    ]
    for item in config.vibe.selectable:
        name = Path(item.file).stem
        lines.append(f"  - {name}：{item.description}" if item.description else f"  - {name}")
    return "\n".join(lines)


def _director_ref_block(config: ImageGeneratorConfig) -> str:
    """构建可选精密参考列表块。"""

    reference = config.director_reference
    if not (reference.enabled and reference.selectable_enabled and reference.selectable):
        return ""
    if not _has_selectable_v4_model(config):
        return ""

    lines = [
        "【可用精密参考列表（通过 selected_director_refs 参数选择，可多选，逗号分隔）】"
    ]
    for item in reference.selectable:
        if not item.enabled:
            continue
        name = item.name or Path(item.file).stem
        lines.append(f"  - {name}：{item.description}" if item.description else f"  - {name}")
    return "\n".join(lines)


def build_draw_description(config: ImageGeneratorConfig) -> str:
    """按当前配置动态检测模型并生成画图 Action 的完整专属描述。

    每次都按当前 config.generation.model 动态重建，注入当前模型标识与专属语法。

    Args:
        config: 已校验的插件配置实例

    Returns:
        画图 Action 的 description 文本
    """
    blocks = [
        _model_header_block(config),
        _model_list_block(config),
        _base_structure_block(),
        _character_naming_block(),
        _all_model_specific_blocks(config),
        _multi_character_block(config),
        _composition_and_filename_block(),
        _style_block(config),
        _negative_block(config),
        _character_block(config),
        _preset_block(config),
        _custom_block(config),
        _vibe_block(config),
        _director_ref_block(config),
    ]
    return "\n\n".join(block for block in blocks if block)


def build_negative_prompt_hint(config: ImageGeneratorConfig) -> str:
    """生成 negative_prompt 参数的动态描述。

    Args:
        config: 已校验的插件配置实例

    Returns:
        参数描述文本
    """
    preset = config.generation.negative_prompt.strip()
    if not preset:
        return "场景专属额外排除词，英文逗号分隔。此处只填本次图片特有的排除内容。"
    return (
        f"场景专属额外排除词，英文逗号分隔。系统已内置：{preset}。"
        "此处只填本次图片特有的排除内容。"
    )


BASE_DRAW_DESCRIPTION = build_draw_description(ImageGeneratorConfig())
