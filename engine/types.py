"""图片生成引擎的数据类型定义。

集中定义引擎层内部流转的结构体：素材、生图请求、执行结果。
这些类型不依赖框架，可被 Action、Command、WebUI 直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DirectorRefType = Literal["character", "style", "character&style"]
DirectorToolType = Literal[
    "declutter",
    "bg-removal",
    "lineart",
    "sketch",
    "colorize",
    "emotion",
]


@dataclass(frozen=True, slots=True)
class VibeAsset:
    """已编码的 Vibe 参考数据。

    Attributes:
        data: NovelAI encode-vibe 产出的向量 base64
        information_extracted: 信息提取量（0.0–1.0）
        strength: 参考强度（0.0–1.0）
        name: 来源文件名（不含后缀），用于日志与状态展示
    """

    data: str
    information_extracted: float
    strength: float
    name: str = ""


@dataclass(frozen=True, slots=True)
class DirectorRefAsset:
    """精密参考（Director Reference）图片数据。

    Attributes:
        data: 已裁剪为 1024x1536 的 PNG base64
        ref_type: 参考类型
        fidelity: 忠实度（0.0–1.0）
        strength: 参考强度（0.0–1.0）
    """

    data: str
    ref_type: DirectorRefType
    fidelity: float
    strength: float


@dataclass(frozen=True, slots=True)
class CharacterPrompt:
    """多人物生图中的单个角色描述。

    Attributes:
        prompt: 该角色的正面标签
        negative_prompt: 该角色的负面标签
        x: 水平坐标（0.0–1.0）
        y: 垂直坐标（0.0–1.0）
    """

    prompt: str
    negative_prompt: str = ""
    x: float = 0.5
    y: float = 0.5


@dataclass(frozen=True, slots=True)
class GenerationSpec:
    """一次文生图 / 图生图请求的完整描述。

    Attributes:
        prompt: 正面提示词
        user_id: 请求发起者标识，用于取用其手动加载的 Vibe
        negative_prompt: 本次额外负面提示词，会与全局负面词合并
        width: 目标宽度
        height: 目标高度
        scale: 覆盖引导比例，None 表示沿用配置值
        cfg_rescale: 覆盖 cfg_rescale，None 表示沿用配置值
        source_image: 图生图原图 base64，非空即视为图生图
        strength: 图生图强度，None 表示沿用配置默认值
        selected_vibe_names: LLM 自选的 Vibe 名称
        director_refs: 精密参考素材
        characters: 多人物列表，仅 V4 系列模型支持
        from_command: 结果是否保存到命令图片目录
    """

    prompt: str
    user_id: str
    negative_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    scale: float | None = None
    cfg_rescale: float | None = None
    source_image: str | None = None
    strength: float | None = None
    selected_vibe_names: tuple[str, ...] = ()
    director_refs: tuple[DirectorRefAsset, ...] = ()
    characters: tuple[CharacterPrompt, ...] = ()
    from_command: bool = False

    @property
    def is_img2img(self) -> bool:
        """是否为图生图请求。"""

        return bool(self.source_image)


@dataclass(frozen=True, slots=True)
class InpaintSpec:
    """一次局部重绘请求的完整描述。

    Attributes:
        prompt: 整张图的完整提示词
        source_image: 源图 base64
        mask: 遮罩 base64（白色区域参与重绘）
        negative_prompt: 本次额外负面提示词
        width: 目标宽度
        height: 目标高度
        strength: 重绘强度（0.01–1.0）
        scale: 覆盖引导比例
        cfg_rescale: 覆盖 cfg_rescale
        from_command: 结果是否保存到命令图片目录
    """

    prompt: str
    source_image: str
    mask: str
    negative_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    strength: float = 0.7
    scale: float | None = None
    cfg_rescale: float | None = None
    from_command: bool = False


@dataclass(frozen=True, slots=True)
class DirectorToolSpec:
    """一次导演工具请求的完整描述。

    Attributes:
        tool_type: 工具类型
        source_image: 源图 base64
        width: 源图宽度
        height: 源图高度
        prompt: colorize / emotion 所需的描述文本
        defry: colorize / emotion 的去模糊强度（0–5）
        from_command: 结果是否保存到命令图片目录
    """

    tool_type: DirectorToolType
    source_image: str
    width: int
    height: int
    prompt: str | None = None
    defry: int | None = None
    from_command: bool = False


@dataclass(frozen=True, slots=True)
class ImageResult:
    """引擎执行结果。

    Attributes:
        success: 是否成功产出图片
        message: 面向用户的说明文本
        path: 成功时的本地图片绝对路径
    """

    success: bool
    message: str
    path: str | None = None

    @classmethod
    def ok(cls, path: str, message: str = "图片生成成功") -> ImageResult:
        """构造成功结果。"""

        return cls(success=True, message=message, path=path)

    @classmethod
    def failure(cls, message: str) -> ImageResult:
        """构造失败结果。"""

        return cls(success=False, message=message, path=None)


@dataclass(slots=True)
class UserVibeStore:
    """按用户隔离的手动 Vibe 缓存。

    Attributes:
        entries: 用户标识到已加载 Vibe 列表的映射
    """

    entries: dict[str, list[VibeAsset]] = field(default_factory=dict)

    def get(self, user_id: str) -> list[VibeAsset]:
        """读取指定用户已加载的 Vibe。"""

        return self.entries.get(user_id, [])

    def add(self, user_id: str, asset: VibeAsset, limit: int) -> tuple[bool, int]:
        """追加一个 Vibe，返回是否成功与当前数量。"""

        loaded = self.entries.setdefault(user_id, [])
        if len(loaded) >= limit:
            return False, len(loaded)
        loaded.append(asset)
        return True, len(loaded)

    def clear(self, user_id: str) -> None:
        """清空指定用户的 Vibe。"""

        self.entries.pop(user_id, None)

    def clear_all(self) -> None:
        """清空全部用户的 Vibe。"""

        self.entries.clear()
