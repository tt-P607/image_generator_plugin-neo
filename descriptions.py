"""画图 Action 描述文本构建。

画图能力的可用画风、预设、参考图及底层生图模型都来自用户配置，
在插件加载或配置热重载时自动检测当前生效模型，并动态组装专属提示词规范
注入到 Action description 和 System Reminder，让模型明确感知当前模型与专属语法。
"""

from __future__ import annotations

from pathlib import Path

from .config import ImageGeneratorConfig
from .engine.types import V3_MODELS, V4_MODELS, V5_MODELS


def detect_model_generation(model: str) -> str:
    """根据固定的确切模型名称判定 NovelAI 模型代际（绝对匹配）。

    Args:
        model: 精确模型名，如 'nai-diffusion-5-curated'

    Returns:
        'v5' | 'v4' | 'v3'
    """
    cleaned = model.strip()
    if cleaned in V5_MODELS:
        return "v5"
    if cleaned in V4_MODELS:
        return "v4"
    if cleaned in V3_MODELS:
        return "v3"
    return "v5"


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
    """构建通用的基础结构与冒号加权说明。"""
    return (
        "**1. 基础结构与分层排布**\n"
        "提示词以半角逗号分隔的英文标签为主，越靠前对画面基调与构图的影响权重越高。\n"
        "推荐九维分层顺序：\n"
        "  1.品质画风 → 2.人数性别 → 3.角色身份 → 4.身体容貌 → "
        "5.服装配饰 → 6.动作表情 → 7.环境背景 → 8.光影构图 → 9.渲染质感\n\n"
        "**2. 权重语法（NovelAI 冒号语法）**\n"
        "  - 提升权重：n::Tag::（推荐 n=1.1~1.4，如 1.3::silver hair::）\n"
        "  - 降低权重：n::Tag::（推荐 n=0.6~0.9，如 0.8::blurry background::）\n"
        "  - 注意：以数字结尾的词条末尾加空格或下划线（如 1.2::2000s _::），防止权重解析粘连"
    )


def _character_naming_block() -> str:
    """构建通用的 Danbooru 角色与皮肤命名规范。"""
    return (
        "**3. 角色与皮肤标签规范**\n"
        "  - 角色标准名：角色名 (作品名)，如 Castorice (honkai: star rail)\n"
        "  - 官方皮肤/形态：角色名_(皮肤名)_(作品名)，如 Hu_Tao_(Cherries_Snow-Laden)_(genshin_impact)\n"
        "  - 游戏 CG 原画风格：1.2::game_name (game cg)::\n"
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


def _model_specific_prompt_block(config: ImageGeneratorConfig) -> str:
    """根据当前模型代际动态生成专属的提示词与文字生成语法。"""
    generation = detect_model_generation(config.generation.model)

    if generation == "v5":
        if _has_selectable_v4_model(config):
            refs_hint = (
                "  - 参考图支持：\n"
                "    V5 不使用 Vibe 风格参考与 Director 角色参考图；如本次需要参考图，"
                "必须在 model 参数中显式指定 V4.5 模型后，再传 selected_vibes / selected_director_refs。"
                "无论哪个模型，多人物坐标（characters 参数）均可用。"
            )
        else:
            refs_hint = (
                "  - 参考图支持：\n"
                "    V5 模型依靠自身强大的提示词语义与原生 Tag 解析能力，"
                "不使用且不支持 Vibe 风格参考与 Director 角色参考图"
                "（无需填写 selected_vibes 与 selected_director_refs）。"
            )
        return (
            "**4. V5 模型专属特性与文字生成**\n"
            "  - 多语言画面文字绘制（V5 核心特性）：\n"
            "    直接使用半角双引号 \"\" 或中文全角引号 “” 包裹想要呈现的文字，可结合自然语言描述载体与位置。\n"
            "    * 对话框文字：green speech bubble that says: “你好，世界！”\n"
            "    * 霓虹灯与招牌：neon street sign with text: \"NOVELAI V5\"\n"
            "    * 横幅与标语：wooden storefront banner writing: “深夜食堂”\n"
            "  - 自然语言融合与光影：\n"
            "    V5 模型对自然语言长句与修饰词理解能力更强，可直接融入丰富的光影描绘（如 volumetric lighting, cinematic lighting, soft warm sunlight filtering through window）。\n"
            "  - 推荐画风品质基调词：\n"
            "    masterpiece, best quality, very aesthetic, official art\n"
            f"{refs_hint}"
        )
    elif generation == "v4":
        return (
            "**4. V4/V4.5 模型专属特性与文字生成**\n"
            "  - 画面文字生成：\n"
            "    必须使用 TEXT: 语法，并配合 speech bubble 等载体标签。\n"
            "    * 示例：speech bubble, TEXT: Hello World\n"
            "  - 标签密集堆叠：\n"
            "    V4/V4.5 侧重标准的 Danbooru Tag 密集组合，对复杂从句容忍度较低，建议将修饰词拆解为独立英文标签。\n"
            "  - 推荐画风品质基调词：\n"
            "    masterpiece, best quality, ultra detailed, 1.2::ultra-detailed CG::\n"
            "  - 参考图支持：\n"
            "    支持 Vibe 风格参考图（selected_vibes）与 Director 角色精确参考图（selected_director_refs）。"
        )
    else:
        return (
            "**4. V3 模型专属特性**\n"
            "  - 纯文本标签：仅支持单角色纯文本 prompt 与 negative_prompt，不支持结构化多角色坐标。\n"
            "  - 推荐画风品质基调词：\n"
            "    masterpiece, best quality, highly detailed"
        )


def _multi_character_block(config: ImageGeneratorConfig) -> str:
    """构建多角色与互动说明。"""
    generation = detect_model_generation(config.generation.model)
    if generation == "v3":
        return (
            "**5. 人物控制**\n"
            "当前 V3 模型仅支持单人物生成，请在 content_description 中直接书写角色与环境标签。"
        )

    return (
        "**5. 多角色与位置控制（5×5 网格坐标系，V5 与 V4/V4.5 均支持）**\n"
        "使用 characters 参数传入 JSON 数组，最多 6 个角色，每项字段：\n"
        "  - prompt：该角色英文专属标签（必填，包括发型、眼睛、服装、专属动作）\n"
        "  - uc：角色专属负面词（可省略）\n"
        "  - x：水平 0.0~1.0（0=最左，1=最右，默认 0.5）\n"
        "  - y：垂直 0.0~1.0（0=最上，1=最下，默认 0.5）\n"
        '示例：[{"prompt":"1girl, blonde hair, white dress","x":0.3,"y":0.5},'
        '{"prompt":"1girl, black hair, blue dress","x":0.7,"y":0.5}]\n\n'
        "**6. 角色互动语法（Interaction Tags）**\n"
        "  - 施动方：source#动作（如 source#hugging，填在发起角色的 prompt）\n"
        "  - 受动方：target#动作（如 target#hugging，填在接受角色的 prompt）\n"
        "  - 相互动作：mutual#动作（如 mutual#holding hands，双方 prompt 均填）\n"
        "  - 规则：source/target 成对出现在不同角色上；不要在 content_description 重复互动动作\n\n"
        "**注意事项**\n"
        "  - 多角色时 content_description 只写环境/光影/构图与人数标签，角色细节放 characters\n"
        "  - ⚠️ 人数标签必须与 characters 精确对应：逐个统计各角色的性别后，把总人数写入 "
        "content_description（如两男一女必须写 2boys, 1girl；两女写 2girls）。"
        "漏写、多写或性别不匹配会导致画面人物数量错乱甚至崩坏\n"
        "  - 单人物生图时不要传 characters，留空即可"
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


# 默认基础描述（展示 V5 架构），供静态引用
BASE_DRAW_DESCRIPTION = "\n\n".join(
    [
        "【当前生效生图模型】\n  模型名称：`nai-diffusion-5-curated`（NovelAI V5 架构）\n  请严格按照该模型对应的专属语法与特性构建提示词。",
        _base_structure_block(),
        _character_naming_block(),
        (
            "**4. V5 模型专属特性与文字生成**\n"
            "  - 多语言画面文字绘制（V5 核心特性）：\n"
            "    直接使用半角双引号 \"\" 或中文全角引号 “” 包裹想要呈现的文字，可结合自然语言描述载体与位置。\n"
            "    * 对话框文字：green speech bubble that says: “你好，世界！”\n"
            "    * 霓虹灯与招牌：neon street sign with text: \"NOVELAI V5\"\n"
            "    * 横幅与标语：wooden storefront banner writing: “深夜食堂”\n"
            "  - 自然语言融合与光影：\n"
            "    V5 模型对自然语言长句与修饰词理解能力更强，可直接融入丰富的光影描绘（如 volumetric lighting, cinematic lighting, soft warm sunlight filtering through window）。\n"
            "  - 推荐画风品质基调词：\n"
            "    masterpiece, best quality, very aesthetic, official art"
        ),
        (
            "**5. 多角色与位置控制（5×5 网格坐标系）**\n"
            "使用 characters 参数传入 JSON 数组，最多 6 个角色，每项字段：\n"
            "  - prompt：该角色英文专属标签（必填，包括发型、眼睛、服装、专属动作）\n"
            "  - uc：角色专属负面词（可省略）\n"
            "  - x：水平 0.0~1.0（0=最左，1=最右，默认 0.5）\n"
            "  - y：垂直 0.0~1.0（0=最上，1=最下，默认 0.5）\n"
            '示例：[{"prompt":"1girl, blonde hair, white dress","x":0.3,"y":0.5},'
            '{"prompt":"1girl, black hair, blue dress","x":0.7,"y":0.5}]\n\n'
            "**6. 角色互动语法（Interaction Tags）**\n"
            "  - 施动方：source#动作（如 source#hugging，填在发起角色的 prompt）\n"
            "  - 受动方：target#动作（如 target#hugging，填在接受角色的 prompt）\n"
            "  - 相互动作：mutual#动作（如 mutual#holding hands，双方 prompt 均填）\n"
            "  - 规则：source/target 成对出现在不同角色上；不要在 content_description 重复互动动作\n\n"
            "**注意事项**\n"
            "  - 多角色时 content_description 只写环境/光影/构图与人数标签，角色细节放 characters\n"
            "  - ⚠️ 人数标签必须与 characters 精确对应：逐个统计各角色的性别后，把总人数写入 "
            "content_description（如两男一女必须写 2boys, 1girl；两女写 2girls）。"
            "漏写、多写或性别不匹配会导致画面人物数量错乱甚至崩坏\n"
            "  - 单人物生图时不要传 characters，留空即可"
        ),
        _composition_and_filename_block(),
    ]
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

    models = config.generation.available_models
    if not models:
        return ""

    default = config.generation.model.strip()
    gen_labels = {"v5": "V5", "v4": "V4/V4.5", "v3": "V3"}
    lines = [
        "【可选模型列表（通过 model 参数指定，不指定则使用默认模型）】"
        "\n  共同能力：多人物坐标定位（characters）、冒号加权语法、三档画幅。"
    ]
    for m in models:
        gen = detect_model_generation(m)
        label = gen_labels.get(gen, "未知")
        suffix = "（默认）" if m == default else ""
        lines.append(f"  - `{m}` [{label}]{suffix}")

    lines.append(
        "  差异提示：V5 独有画面文字直接引号语法；"
        "仅 V4/V4.5 支持画面文字 TEXT: 语法、Vibe 与精密参考。"
        "需要参考图时切换到 V4.5 模型并传 selected_vibes / selected_director_refs。"
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
        _model_specific_prompt_block(config),
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
