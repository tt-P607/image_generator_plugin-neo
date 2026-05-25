"""图片生成插件配置定义。

定义插件所有可配置参数，基于 Pydantic + TOML 热重载。
通过 @config_section 划分为语义清晰的 Section。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class VibeItemConfig(SectionBase):
    """单个 Vibe 配置项，always 和 selectable 列表通用。"""

    file: str = Field(
        description="vibes/ 目录下的文件名（支持 .naiv4vibe / .naiv4vibebundle / .png / .jpg）",
    )
    enabled: bool = Field(
        default=True,
        description="是否启用此 Vibe（设为 false 可临时禁用而不需要删除配置）",
    )
    description: str = Field(
        default="",
        description=(
            "该 Vibe 的场景描述，告诉模型什么时候适合使用此画风。"
            "例如：'适合赛博朋克、科幻、迷幻场景' 或 '适合温馨日常、治愈系场景'"
        ),
    )
    ie: float = Field(
        default=1.0,
        description="information_extracted：提取的信息量（0.0–1.0）",
    )
    strength: float = Field(
        default=0.6,
        description="参考强度（0.0–1.0）",
    )


class DirectorReferenceItemConfig(SectionBase):
    """单个精密参考配置项。"""

    file: str = Field(
        description="vibes/ 目录下的文件名（支持 .png / .jpg）",
    )
    enabled: bool = Field(
        default=True,
        description="是否启用此参考图",
    )
    name: str = Field(
        default="",
        description="参考图名称，供 LLM 选择。如果留空则使用文件名（不含后缀）。",
    )
    description: str = Field(
        default="",
        description="该参考图的场景/形态描述，告诉模型什么时候适合使用此参考。",
    )
    type: str = Field(
        default="character&style",
        description="参考类型：character, style, 或 character&style",
    )
    fidelity: float = Field(
        default=1.0,
        description="忠实度（0.0–1.0）",
    )
    strength: float = Field(
        default=1.0,
        description="参考强度（0.0–1.0）",
    )


class PromptPresetConfig(SectionBase):
    """单条提示词预设配置。

    用于定义特定场景下的生图指令，模型会根据 trigger 条件判断是否应用。
    """

    name: str = Field(
        description="预设名称，如 '自拍模式'、'表情包模式'",
    )
    trigger: str = Field(
        default="",
        description=(
            "触发条件描述，告诉模型什么时候应该使用此预设。"
            "例如：'画自己/自拍时' 或 '用户要表情包时'"
        ),
    )
    content: str = Field(
        default="",
        description=(
            "预设内容，具体的提示词指令。"
            "例如：'必须包含角色标签 xxx (source)，优先使用当前时段服装标签'"
        ),
    )


class ImageGeneratorConfig(BaseConfig):
    """图片生成插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "NovelAI/IdleCloud 图片生成插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件基础配置。"""

        enabled: bool = Field(
            default=False,
            description="是否启用插件",
        )

    @config_section("components")
    class ComponentsSection(SectionBase):
        """组件开关配置。"""

        action_enabled: bool = Field(
            default=True,
            description="是否启用 Action 组件（实际执行生图）",
        )
        command_enabled: bool = Field(
            default=True,
            description="是否启用 Command 组件（/画图 等命令）",
        )

    @config_section("api")
    class ApiSection(SectionBase):
        """API 连接配置。"""

        channel: str = Field(
            default="official",
            description=(
                "生图渠道选择，决定插件使用哪套 API 协议。端点统一由 base_url 配置。\n\n"
                "  official（默认）\n"
                "    直连 NovelAI 官方 API 协议，base_url 填写生图端点完整路径。\n"
                "    支持全部功能：Vibe Transfer、Director Reference、图生图、多人物坐标等。\n"
                "    响应格式：ZIP/PNG 二进制，插件自动解压保存。\n"
                "    示例 base_url：https://image.novelai.net/ai/generate-image\n\n"
                "  gateway\n"
                "    使用 OpenAI Chat Completions 兼容协议（novelai-gateway 中转服务）。\n"
                "    base_url 填写 gateway 服务根地址，插件自动拼接 /v1/chat/completions。\n"
                "    支持含 /v1 后缀的完整路径，插件会自动规范化。\n"
                "    支持：正面/负面提示词、多人物坐标、scale/cfg_rescale/画幅/采样器等参数。\n"
                "    不支持：Vibe Transfer、Director Reference、图生图（切换后自动跳过，不报错）。\n"
                "    响应格式：Markdown 图片链接，插件自动下载保存。\n"
                "    示例 base_url：http://127.0.0.1:31555 或 https://your-gateway.example.com/v1"
            ),
        )
        api_keys: list[str] = Field(
            default_factory=list,
            description="API Keys 列表（支持多个，会自动轮换）。key 格式由所用渠道决定。",
        )
        base_url: str = Field(
            default="https://image.novelai.net/ai/generate-image",
            description=(
                "API 端点 URL，两种渠道均使用此字段。\n"
                "official 渠道：填写完整的生图端点，如 https://image.novelai.net/ai/generate-image。\n"
                "gateway 渠道：填写服务根地址（含或不含 /v1 均可），"
                "如 http://127.0.0.1:31555 或 https://your-gateway.example.com/v1。"
            ),
        )
        proxy: str = Field(
            default="",
            description="代理地址（如 http://127.0.0.1:7890），留空则不使用代理",
        )
        cooldown: int = Field(
            default=20,
            description="请求冷却时间（秒）",
        )

    @config_section("generation")
    class GenerationSection(SectionBase):
        """生图参数配置。"""

        model: str = Field(
            default="nai-diffusion-4-5-curated",
            description="绘图模型",
        )
        noise_schedule: str = Field(
            default="karras",
            description="噪声调度",
        )
        resolution: str = Field(
            default="1024x1024",
            description="默认画布分辨率",
        )
        steps: int = Field(
            default=28,
            description="迭代步数",
        )
        scale: float = Field(
            default=5.0,
            description="引导比例",
        )
        sampler: str = Field(
            default="k_euler_ancestral",
            description="采样器",
        )
        prompt_guidance_rescale: float = Field(
            default=0.0,
            description="提示词引导重新缩放比例",
        )
        uc_preset: int = Field(
            default=0,
            description=(
                "Undesired Content 预设（官网 UC Preset 下拉）。"
                "0=Strong（默认通用质量负面词），1=Light，2=Furry Focus，"
                "3=Human Focus（推荐正常比例人物，防止 Q 版），4=None（不追加）。"
            ),
        )
        variety_plus: bool = Field(
            default=False,
            description=(
                "是否启用 Variety+（skip_cfg_above_sigma）。"
                "开启后引导只在主体成型后介入，增加出图多样性，但提示词贴合度会略有下降。"
            ),
        )
        style_reference: str = Field(
            default="",
            description=(
                "画风标签，自动拼接到所有提示词最前面。"
                "用于统一画面风格基调，可根据模型特性调整。"
            ),
        )
        negative_prompt: str = Field(
            default=(
                "bad anatomy, extra fingers, six fingers, mutated hands, poorly drawn hands, "
                "extra limbs, disfigured, malformed, missing fingers, extra digit, fewer digits, "
                "bad proportions, deformed, worst quality, low quality, normal quality, "
                "jpeg artifacts, signature, watermark, username, text, blurry, logo, "
                "brand name, copyright name, artist name, fake watermark"
            ),
            description=(
                "全局负面提示词（防止肢体扭曲如六指等，"
                "禁止 logo 和水印，AI 会在此基础上添加特殊场景的负面词）"
            ),
        )
        character_prompt: str = Field(
            default="",
            description=(
                "角色外观描述（自由文本），用于告诉模型 Bot 自己长什么样。"
                "画自己/自拍时模型会参考此描述来构建提示词。"
                "可以用自然语言描述外貌特征，也可以直接写标签，格式不限。"
            ),
        )
        always_use_coords: bool = Field(
            default=True,
            description=(
                "多人物生图时是否始终启用坐标定位（use_coords=true）。"
                "开启后 LLM 提供的 (x, y) 严格生效；关闭则交给模型自由排布，"
                "建议保持开启以匹配 NovelAI Web UI multi-character workspace 行为。"
            ),
        )

    @config_section("advanced")
    class AdvancedSection(SectionBase):
        """高级配置。"""

        temp_dir: str = Field(
            default="data/image_generator_plugin-neo/temp_images",
            description="临时图片保存目录（相对于项目根目录）",
        )
        vibe_storage_dir: str = Field(
            default="data/image_generator_plugin-neo/vibes",
            description="Vibe 素材存储目录（相对于项目根目录）",
        )
        command_images_dir: str = Field(
            default="data/image_generator_plugin-neo/command_images",
            description="命令生成图片保存目录（相对于项目根目录）",
        )
        max_vibes: int = Field(
            default=4,
            description="最大 Vibe 叠加数量",
        )
        img2img_default_strength: float = Field(
            default=0.7,
            description="图生图默认强度",
        )
        max_characters: int = Field(
            default=6,
            description=(
                "单次生图允许的最大角色数（NovelAI Web UI multi-character workspace 默认上限为 6，"
                "超过该数量会被拒绝；仅 V4 系列模型支持，V3 调用多人物会直接报错）。"
            ),
        )

    @config_section("prompt")
    class PromptSection(SectionBase):
        """提示词与预设配置。

        支持三种方式向模型注入生图指引：
        - custom_instructions：自由文本区块，用户可写任意指引内容
        - presets：结构化预设列表，每条带名称、触发条件、具体内容
        """

        custom_instructions: str = Field(
            default="",
            description=(
                "自定义提示词指引（自由文本），会原样追加到 Action description 末尾。\n"
                "可以写任何你想让模型在画图时遵循的规则或偏好，\n"
                "例如：画风偏好、禁止事项、特殊场景处理方式等。\n"
                "支持多行，内容会直接展示给模型。"
            ),
        )
        presets: list[PromptPresetConfig] = Field(
            default_factory=list,
            description=(
                "结构化预设列表，每条预设包含名称、触发条件和具体内容。\n"
                "模型会根据触发条件判断当前场景是否适用该预设。\n"
                "例如：自拍模式、表情包模式、风景模式等。"
            ),
        )

    @config_section("vibe")
    class VibeSection(SectionBase):
        """Vibe 参考图注入配置。

        - always：每次生图都注入，适合固定风格底图。
        - selectable：供 LLM 按场景自选，带场景描述帮助模型选择。
        """

        always_enabled: bool = Field(
            default=False,
            description="启用始终注入模式（每次生图都注入 always 列表中的 Vibe）",
        )
        selectable_enabled: bool = Field(
            default=False,
            description="启用 LLM 自选模式（draw_image 新增 selected_vibes 参数，LLM 可按场景选择 selectable 中的画风）",
        )
        always: list[VibeItemConfig] = Field(
            default_factory=list,
            description="始终注入的 Vibe 列表（always_enabled=true 时每次生图都使用）",
        )
        selectable: list[VibeItemConfig] = Field(
            default_factory=list,
            description="LLM 可自选的 Vibe 列表（selectable_enabled=true 时生效，带场景描述帮助模型选择）",
        )

    @config_section("director_reference")
    class DirectorReferenceSection(SectionBase):
        """精密参考 (Director Reference) 注入配置。"""

        enabled: bool = Field(
            default=False,
            description="是否启用精密参考功能（总开关）",
        )
        selectable_enabled: bool = Field(
            default=False,
            description="是否允许 LLM 自选精密参考图",
        )
        selectable: list[DirectorReferenceItemConfig] = Field(
            default_factory=list,
            description="LLM 可自选的精密参考列表",
        )

    @config_section("wardrobe")
    class WardrobeSection(SectionBase):
        """每日服装系统配置。

        混合模式：用户在 wardrobe_file 中手动定义衣柜（精确 tags），
        每天由 LLM 根据季节/日期类型从衣柜中选择最合适的三时段搭配。
        """

        enabled: bool = Field(
            default=False,
            description="是否启用每日服装系统",
        )
        refresh_time: str = Field(
            default="06:00",
            description="每天刷新服装的时间（HH:MM 24h 格式）",
        )
        generate_model: str = Field(
            default="",
            description="选择服装所用模型名称（对应 model.toml 里的 name）；留空使用 ACTOR 任务默认模型",
        )
        wardrobe_file: str = Field(
            default="data/image_generator_plugin-neo/wardrobe.json",
            description="衣柜定义文件路径（用户手动维护，定义所有可选套装）",
        )
        state_file: str = Field(
            default="data/image_generator_plugin-neo/wardrobe_state.json",
            description="每日穿搭状态文件路径（系统自动更新，记录今天选择了哪套）",
        )
        daytime_start: int = Field(
            default=6,
            description="白天时段起始小时（含，24h）",
        )
        evening_start: int = Field(
            default=18,
            description="傍晚时段起始小时（含，24h）",
        )
        night_start: int = Field(
            default=22,
            description="深夜时段起始小时（含，24h）",
        )
        holiday_aware: bool = Field(
            default=False,
            description="是否启用节假日感知（开启后会检测中国法定节假日，影响当天的穿搭选择）",
        )
        history_days: int = Field(
            default=3,
            description=(
                "生成穿搭时参考的历史天数。系统会将最近 N 天的穿搭记录传给 LLM，"
                "要求避免重复搭配，增加每日穿搭多样性。设为 0 则不参考历史。"
            ),
        )
        selection_instructions: str = Field(
            default=(
                "你正在以角色本人的视角为自己挑选今天的穿搭。请像真人一样思考穿什么：\n"
                "1. 季节适配：夏天不穿大衣和围巾，冬天不穿吊带和短裤\n"
                "2. 时段合理：白天穿外出或日常服，傍晚可以换舒适装，深夜穿睡衣或居家服\n"
                "3. 场景匹配：工作日偏正式/日常，周末偏休闲/运动/约会\n"
                "4. 风格协调：上下装、鞋子、配饰之间风格统一，不要混搭冲突\n"
                "5. 三时段有变化：不要三个时段穿一模一样的，至少深夜要换成居家/睡衣\n"
                "6. 符合角色性格：根据角色人设选择符合其审美和气质的搭配"
            ),
            description=(
                "LLM 选择每日穿搭时的额外指引（自由文本）。\n"
                "会作为 system prompt 的一部分注入，指导 LLM 如何合理搭配。\n"
                "可以写搭配规则、风格偏好、禁忌等。"
            ),
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    components: ComponentsSection = Field(default_factory=ComponentsSection)
    api: ApiSection = Field(default_factory=ApiSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    advanced: AdvancedSection = Field(default_factory=AdvancedSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    vibe: VibeSection = Field(default_factory=VibeSection)
    director_reference: DirectorReferenceSection = Field(default_factory=DirectorReferenceSection)
    wardrobe: WardrobeSection = Field(default_factory=WardrobeSection)
