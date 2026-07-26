"""配置持久化兼容边界。

公开的 ``config_api`` 目前不支持把任意已校验的配置实例写回 TOML，
因此这里集中封装渲染、语法校验与原子替换。这是本插件唯一依赖
框架内部实现的位置，一旦公开 API 支持保存实例即可整体移除。
"""

from __future__ import annotations

import os
import tomllib
import uuid
from pathlib import Path
from typing import cast

from src.kernel.config.core import ConfigBase, _render_toml_with_signature

from ..config import ImageGeneratorConfig


def render_config(config: ImageGeneratorConfig) -> str:
    """把配置实例渲染为带签名的 TOML 文本。

    Args:
        config: 已校验的配置实例

    Returns:
        TOML 文本
    """
    return _render_toml_with_signature(
        cast(type[ConfigBase], ImageGeneratorConfig),
        config.model_dump(mode="python"),
    )


def save_config_atomically(path: Path, config: ImageGeneratorConfig) -> None:
    """校验并原子写入配置文件。

    先写临时文件并用 tomllib 复核语法，确认无误后再替换目标文件，
    避免渲染异常把线上配置写坏。

    Args:
        path: 目标配置文件路径
        config: 已校验的配置实例
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(render_config(config), encoding="utf-8")
        with temp_path.open("rb") as stream:
            tomllib.load(stream)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
