"""图片生成插件配置定义。

定义插件所有可配置参数，基于 Pydantic + TOML 热重载。
通过 @config_section 划分为语义清晰的 Section。
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class VibePresetConfig(SectionBase):
    """单个 Vibe 预设项配置。

    用于 [[vibe.presets]] 列表中的每一条记录。
    """

    file: str = Field(
        description="vibes/ 目录下的文件名（支持 .naiv4vibe / .naiv4vibebundle / .png / .jpg）",
    )
    ie: float = Field(
        default=1.0,
        description="information_extracted：提取的信息量（0.0–1.0），越高引用越多画面内容",
    )
    strength: float = Field(
        default=0.6,
        description="参考强度（0.0–1.0），越高生成结果越贴近参考图风格",
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
            description="是否启用 Action 组件（LLM Tool Calling 画图）",
        )
        command_enabled: bool = Field(
            default=True,
            description="是否启用 Command 组件（/画图 等命令）",
        )

    @config_section("api")
    class ApiSection(SectionBase):
        """API 连接配置。"""

        api_keys: list[str] = Field(
            default_factory=list,
            description="NovelAI API Keys 列表（支持多个，会自动轮换）",
        )
        base_url: str = Field(
            default="https://image.novelai.net/ai/generate-image",
            description="API 基础 URL",
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
            default="k_euler",
            description="采样器",
        )
        prompt_guidance_rescale: float = Field(
            default=0.0,
            description="提示词引导重新缩放比例",
        )
        negative_prompt: str = Field(
            default=(
                "nsfw, nude, naked, r18, porn, sexual, explicit, adult content, "
                "bad anatomy, extra fingers, six fingers, mutated hands, poorly drawn hands, "
                "extra limbs, disfigured, malformed, missing fingers, extra digit, fewer digits, "
                "bad proportions, deformed, worst quality, low quality, normal quality, "
                "jpeg artifacts, signature, watermark, username, text, blurry, logo, "
                "brand name, copyright name, artist name, fake watermark"
            ),
            description=(
                "通用负面提示词（禁止 NSFW/R18 内容，防止肢体扭曲如六指等，"
                "禁止 logo 和水印，AI 会在此基础上添加特殊场景的负面词）"
            ),
        )
        character_prompt: str = Field(
            default="1girl, beautiful detailed eyes, long pink hair, blue eyes, elf ears",
            description="AI 自拍功能的角色特征锚定（用于生成 Bot 自己的照片，确保生成的是特定角色外观）",
        )

    @config_section("advanced")
    class AdvancedSection(SectionBase):
        """高级配置。"""

        temp_dir: str = Field(
            default="temp_images",
            description="临时图片保存目录（相对于插件目录）",
        )
        vibe_storage_dir: str = Field(
            default="vibes",
            description="Vibe 素材存储目录（相对于插件目录）",
        )
        command_images_dir: str = Field(
            default="command_images",
            description="命令生成图片保存目录（相对于插件目录）",
        )
        max_vibes: int = Field(
            default=4,
            description="最大 Vibe 叠加数量",
        )
        img2img_default_strength: float = Field(
            default=0.7,
            description="图生图默认强度",
        )

    @config_section("prompt")
    class PromptSection(SectionBase):
        """自定义提示词配置。"""

        custom_instructions: str = Field(
            default="",
            description=(
                "追加到 draw_image 和 generate_selfie 两个 action 描述末尾的自定义指令。\n"
                "可描述希望 AI 主动使用这两个功能的具体场景，"
                "例如：用户想要表情包、壁纸、特定内容的图片时主动生成。\n"
                "不会覆盖已有的触发条件，只是扩充场景说明。"
            ),
        )

    @config_section("vibe")
    class VibeSection(SectionBase):
        """Vibe 参考图自动注入配置。

        支持两种模式（可同时开启）：
        - manual_enabled：无论何时生图都始终注入下方 presets 列表中的 Vibe。
        - auto_select：在 draw_image Action 的参数中新增 use_preset_vibes 选项，
          让 LLM 自行判断当前场景是否需要使用 Vibe 预设（需配合 action_description
          告知 AI 在什么情况下启用）。

        注意：两种模式都依赖 presets 列表，列表为空则不生效。
        """

        manual_enabled: bool = Field(
            default=False,
            description="始终注入 Vibe 预设（每次生图都使用 presets 列表中的 Vibe）",
        )
        auto_select: bool = Field(
            default=False,
            description=(
                "允许 LLM 自行决定是否使用 Vibe 预设。\n"
                "开启后 draw_image Action 会增加 use_preset_vibes 布尔参数，"
                "LLM 可根据场景选择是否附加 Vibe。"
            ),
        )
        presets: list[VibePresetConfig] = Field(
            default_factory=list,
            description=(
                "Vibe 预设列表，每项对应 vibes/ 目录中的一个文件。\n"
                "manual_enabled 或 LLM 选择 use_preset_vibes=true 时，"
                "列表中所有 Vibe 都会被注入到生图请求。"
            ),
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    components: ComponentsSection = Field(default_factory=ComponentsSection)
    api: ApiSection = Field(default_factory=ApiSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    advanced: AdvancedSection = Field(default_factory=AdvancedSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    vibe: VibeSection = Field(default_factory=VibeSection)
