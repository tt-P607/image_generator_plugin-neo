"""引擎运行时配置快照。

把插件 TOML 配置收敛成一个不可变快照，供引擎各子模块共享。
配置热重载时整体替换快照，避免散落的可变字段彼此不同步。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import ImageGeneratorConfig

OFFICIAL_GENERATE_PATH = "/ai/generate-image"
OFFICIAL_ENCODE_VIBE_PATH = "/ai/encode-vibe"
OFFICIAL_AUGMENT_PATH = "/ai/augment-image"
OFFICIAL_UPSCALE_PATH = "/ai/upscale"
OFFICIAL_SUBSCRIPTION_PATH = "/user/subscription"


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """引擎所需的全部配置项快照。

    Attributes:
        channel: 生图渠道，"official" 或 "gateway"
        api_keys: API Key 列表，按序轮换
        base_url: 渠道端点，official 为生图完整路径，gateway 为服务根地址
        api_base_url: official 渠道的 API 域名
        proxy: HTTP 代理地址，空串表示不使用
        cooldown: 两次请求之间的最小间隔秒数
        model: 绘图模型名
        noise_schedule: 噪声调度
        resolution: 默认画布分辨率
        steps: 迭代步数
        scale: 引导比例
        sampler: 采样器
        cfg_rescale: 提示词引导重新缩放比例
        uc_preset: Undesired Content 预设编号
        variety_plus: 是否启用 Variety+
        negative_prompt: 全局负面提示词
        always_use_coords: 多人物时是否启用坐标定位
        max_characters: 单次生图允许的最大角色数
        max_vibes: 单用户最大 Vibe 叠加数
        img2img_default_strength: 图生图默认强度
        img2img_auto_downscale: 图生图是否自动缩放到免费像素范围
        temp_dir: Action 产图保存目录
        command_images_dir: Command 产图保存目录
        vibe_storage_dir: Vibe 素材目录
        vibe_always_enabled: 是否启用 always Vibe 注入
        vibe_selectable_enabled: 是否允许 LLM 自选 Vibe
    """

    channel: str
    api_keys: tuple[str, ...]
    base_url: str
    api_base_url: str
    proxy: str
    cooldown: int

    model: str
    noise_schedule: str
    resolution: str
    steps: int
    scale: float
    sampler: str
    cfg_rescale: float
    uc_preset: int
    variety_plus: bool
    negative_prompt: str
    always_use_coords: bool
    max_characters: int

    max_vibes: int
    img2img_default_strength: float
    img2img_auto_downscale: bool

    temp_dir: Path
    command_images_dir: Path
    vibe_storage_dir: Path

    vibe_always_enabled: bool
    vibe_selectable_enabled: bool

    @classmethod
    def from_config(cls, config: ImageGeneratorConfig) -> EngineSettings:
        """从插件配置构造引擎快照。

        Args:
            config: 已校验的插件配置实例

        Returns:
            引擎运行时配置快照
        """
        return cls(
            channel=config.api.channel,
            api_keys=tuple(config.api.api_keys),
            base_url=config.api.base_url,
            api_base_url=config.api.api_base_url,
            proxy=config.api.proxy,
            cooldown=config.api.cooldown,
            model=config.generation.model,
            noise_schedule=config.generation.noise_schedule,
            resolution=config.generation.resolution,
            steps=config.generation.steps,
            scale=config.generation.scale,
            sampler=config.generation.sampler,
            cfg_rescale=config.generation.prompt_guidance_rescale,
            uc_preset=config.generation.uc_preset,
            variety_plus=config.generation.variety_plus,
            negative_prompt=config.generation.negative_prompt,
            always_use_coords=config.generation.always_use_coords,
            max_characters=config.advanced.max_characters,
            max_vibes=config.advanced.max_vibes,
            img2img_default_strength=config.advanced.img2img_default_strength,
            img2img_auto_downscale=config.generation.img2img_auto_downscale,
            temp_dir=Path(config.advanced.temp_dir).absolute(),
            command_images_dir=Path(config.advanced.command_images_dir).absolute(),
            vibe_storage_dir=Path(config.advanced.vibe_storage_dir).absolute(),
            vibe_always_enabled=config.vibe.always_enabled,
            vibe_selectable_enabled=config.vibe.selectable_enabled,
        )

    @property
    def is_gateway(self) -> bool:
        """当前是否使用 Gateway 渠道。"""

        return self.channel == "gateway"

    @property
    def is_v4_model(self) -> bool:
        """当前模型是否属于 NovelAI V4 系列。"""

        return "diffusion-4" in self.model

    @property
    def gateway_root(self) -> str:
        """Gateway 服务根地址，已去除末尾的 /v1 与斜杠。"""

        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root

    def gateway_url(self, path: str) -> str:
        """拼接 Gateway 端点完整 URL。

        Args:
            path: 以 / 开头的端点路径，如 "/v1/images/generations"

        Returns:
            完整请求 URL
        """
        return f"{self.gateway_root}{path}"

    @property
    def official_generate_url(self) -> str:
        """official 渠道生图端点。"""

        return self.base_url

    @property
    def official_encode_vibe_url(self) -> str:
        """official 渠道 Vibe 编码端点。"""

        return self.base_url.replace(
            OFFICIAL_GENERATE_PATH,
            OFFICIAL_ENCODE_VIBE_PATH,
        )

    @property
    def official_augment_url(self) -> str:
        """official 渠道导演工具端点。"""

        return f"{self.api_base_url.rstrip('/')}{OFFICIAL_AUGMENT_PATH}"

    @property
    def official_upscale_url(self) -> str:
        """official 渠道 4x 放大端点。"""

        return f"{self.api_base_url.rstrip('/')}{OFFICIAL_UPSCALE_PATH}"

    @property
    def official_subscription_url(self) -> str:
        """official 渠道订阅信息端点。"""

        return f"{self.api_base_url.rstrip('/')}{OFFICIAL_SUBSCRIPTION_PATH}"

    def output_dir(self, from_command: bool) -> Path:
        """按调用来源选择图片保存目录。

        Args:
            from_command: 是否来自命令调用

        Returns:
            目标保存目录
        """
        return self.command_images_dir if from_command else self.temp_dir
