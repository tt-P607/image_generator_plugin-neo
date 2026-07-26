"""配置、manifest 与组件声明测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.plugin import ImageGeneratorPlugin

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_version_dependencies_and_components() -> None:
    """验证版本、依赖和组件声明与源码一致。"""

    manifest = json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.0.7"
    assert manifest["python_dependencies"] == ["aiohttp", "numpy", "pillow>=12.1.0"]
    declared = {
        (item["component_type"], item["component_name"])
        for item in manifest["include"]
    }
    assert ("command", "nai_ref") in declared
    assert sum(kind == "action" for kind, _ in declared) == 8
    assert sum(kind == "command" for kind, _ in declared) == 4
    assert sum(kind == "service" for kind, _ in declared) == 1
    assert sum(kind == "router" for kind, _ in declared) == 1
    assert sum(kind == "config" for kind, _ in declared) == 1


def test_plugin_components_match_manifest() -> None:
    """验证默认配置暴露的组件名称。"""

    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.webui.enabled = True
    config.components.director_bg_removal_enabled = True
    plugin = ImageGeneratorPlugin(config)
    components = plugin.get_components()
    names: set[str] = set()
    for component in components:
        comp_name = getattr(component, "name", None)
        if isinstance(comp_name, str) and comp_name:
            names.add(comp_name)
    assert {
        "draw_image",
        "inpaint_image",
        "director_declutter",
        "director_bg_removal",
        "director_lineart",
        "director_sketch",
        "director_colorize",
        "director_emotion",
        "nai_image",
        "nai_edit",
        "nai_ref",
        "nai_vibe",
        "image_generator",
        "image_generator_webui",
    }.issubset(names)
    assert ImageGeneratorConfig in plugin.configs


def test_config_rejects_invalid_values() -> None:
    """验证主要参数范围。"""

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["generation"]["steps"] = 0
    with pytest.raises(ValidationError):
        ImageGeneratorConfig.model_validate(raw)

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["api"]["channel"] = "unknown"
    with pytest.raises(ValidationError):
        ImageGeneratorConfig.model_validate(raw)


def test_config_discards_empty_vibe_placeholders() -> None:
    """验证框架渲染器生成的空 Vibe 占位项不会阻断配置加载。"""

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["vibe"]["always"] = [
        {
            "file": "",
            "enabled": True,
            "description": "",
            "ie": 1.0,
            "strength": 0.6,
        }
    ]
    raw["vibe"]["selectable"] = [
        {
            "file": "   ",
            "enabled": True,
            "description": "",
            "ie": 1.0,
            "strength": 0.6,
        },
        {
            "file": "valid.naiv4vibe",
            "enabled": True,
            "description": "有效项",
            "ie": 1.0,
            "strength": 0.6,
        },
    ]

    config = ImageGeneratorConfig.model_validate(raw)

    assert config.vibe.always == []
    assert len(config.vibe.selectable) == 1
    assert config.vibe.selectable[0].file == "valid.naiv4vibe"


def test_config_list_defaults_are_isolated() -> None:
    """验证列表默认值不在实例间共享。"""

    first = ImageGeneratorConfig()
    second = ImageGeneratorConfig()
    first.api.api_keys.append("pst-test")
    assert second.api.api_keys == []
