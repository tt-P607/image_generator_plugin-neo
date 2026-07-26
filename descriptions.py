"""画图 Action 描述文本构建。

画图能力的可用画风、预设、参考图都来自用户配置，需要在插件加载时
拼进 Action description 让模型看到。本模块只负责生成文本，
不修改任何全局状态。
"""

from __future__ import annotations

from pathlib import Path

from .config import ImageGeneratorConfig

BASE_DRAW_DESCRIPTION = (
    "**1. 基础结构**\n"
    "提示词必须为英文、半角逗号分隔的标签式结构。越靠前权重越高。\n"
    "推荐顺序：风格 → 人数/性别 → 角色身份 → 身体特征 → "
    "服装配饰 → 动作表情 → 环境背景 → 光影视角\n\n"
    "**2. 权重语法（NovelAI 冒号语法）**\n"
    "  提升：n::Tag::（推荐 n=1.1~1.4）\n"
    "  降低：n::Tag::（推荐 n=0.6~0.9）\n"
    "  旧版：{Tag} ≈x1.1，[Tag] ≈x0.9，建议弃用\n"
    "  注意：以数字结尾的词条末尾需加空格或下划线，否则权重会影响后续词条\n\n"
    "**3. 角色与皮肤标签**\n"
    "  角色：角色名 (作品名)，如 Castorice (honkai: star rail)\n"
    "  皮肤：角色名_(皮肤名)_(作品名)，如 Hu_Tao_(Cherries_Snow-Laden)_(genshin_impact)\n"
    "  游戏 CG 风格：1.2::game_name (game cg)::\n"
    "  大小写不敏感，特殊符号需还原\n\n"
    "**4. 画面文字**\n"
    "  语法：TEXT: 要显示的文字（TEXT 大写）\n"
    "  可多次使用，配合 speech bubble 实现漫画效果\n\n"
    "**5. 多角色与位置控制**\n"
    "使用 characters 参数传入 JSON 数组，最多 6 个角色，每项字段：\n"
    "  - prompt：该角色英文标签（必填）\n"
    "  - uc：角色专属负面词（可省略）\n"
    "  - x：水平 0.0~1.0（0=最左，1=最右，默认 0.5）\n"
    "  - y：垂直 0.0~1.0（0=最上，1=最下，默认 0.5）\n"
    '示例：[{"prompt":"1girl, blonde hair","x":0.3,"y":0.5},'
    '{"prompt":"1girl, black hair","x":0.7,"y":0.5}]\n\n'
    "**6. 互动标签**\n"
    "  施动方：source#动作（如 source#hugging）\n"
    "  受动方：target#动作（如 target#hugging）\n"
    "  相互：mutual#动作（如 mutual#holding hands）\n"
    "  规则：source/target 必须成对出现在不同角色上；"
    "不要在 content_description 重复互动动作；"
    "同一对不能同时用 source# 和 mutual#\n\n"
    "**7. 精确参考 (Director Reference)**\n"
    "使用 selected_director_refs 参数，填入要使用的参考图名称（逗号分隔）。\n"
    "可用名称由系统在上下文中提供，不在列表中的名称无效。\n\n"
    "**注意事项**\n"
    "  - 多角色时 content_description 只写环境/光影/构图/人数，角色细节放 characters\n"
    "  - 单人物不要传 characters，留空即可\n"
    "  - 仅 V4 系列模型支持多人物和精确参考\n\n"
    "**画幅**：人物竖图 832x1216，风景横图 1216x832，方形 1024x1024\n\n"
    "**文件名规范**（output_filename，必填）：\n"
    "  每次出图必须指定文件名，仅英文/数字/下划线，不含扩展名。\n"
    "  命名建议：内容描述_序号，如 character_portrait_01、landscape_sunset_02。\n"
    "  出图成功后返回值包含实际文件名，后续 inpaint_image / director_tool "
    "可通过 image_filename 引用。"
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


def _vibe_block(config: ImageGeneratorConfig) -> str:
    """构建可选 Vibe 列表块。"""

    if not config.vibe.selectable_enabled or not config.vibe.selectable:
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
    """按当前配置生成画图 Action 的完整描述。

    每次都从固定基础文本重建，保证配置热重载后不会叠加历史内容。

    Args:
        config: 已校验的插件配置实例

    Returns:
        画图 Action 的 description 文本
    """
    blocks = [
        BASE_DRAW_DESCRIPTION,
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
