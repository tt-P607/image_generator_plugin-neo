"""Image Generator 配置持久化兼容边界。

公开配置 API 暂不支持保存任意已校验实例，因此在此集中封装 TOML
渲染、语法校验和原子替换，避免 Router 与 WebUI 直接依赖私有实现。
"""

from __future__ import annotations

import os
import tomllib
import uuid
from pathlib import Path
from typing import cast

from src.kernel.config.core import ConfigBase, _render_toml_with_signature

from .config import ImageGeneratorConfig


def render_config(config: ImageGeneratorConfig) -> str:
    """将图片生成配置渲染为 TOML 文本。"""

    return _render_toml_with_signature(
        cast(type[ConfigBase], ImageGeneratorConfig),
        config.model_dump(mode="python"),
    )


def save_config_atomically(path: Path, config: ImageGeneratorConfig) -> None:
    """校验并原子保存图片生成配置。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(render_config(config), encoding="utf-8")
        with temp_path.open("rb") as stream:
            tomllib.load(stream)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


__all__ = ["render_config", "save_config_atomically"]
