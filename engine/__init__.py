"""图片生成引擎包。

对外只暴露引擎实例与请求/结果类型，内部子模块（http、queue、payload、
storage、assets、settings）视为实现细节。
"""

from .engine import ImageEngine
from .settings import EngineSettings
from .types import (
    CharacterPrompt,
    DirectorRefAsset,
    DirectorRefType,
    DirectorToolSpec,
    DirectorToolType,
    GenerationSpec,
    ImageResult,
    InpaintSpec,
    VibeAsset,
)

__all__ = [
    "CharacterPrompt",
    "DirectorRefAsset",
    "DirectorRefType",
    "DirectorToolSpec",
    "DirectorToolType",
    "EngineSettings",
    "GenerationSpec",
    "ImageEngine",
    "ImageResult",
    "InpaintSpec",
    "VibeAsset",
]
