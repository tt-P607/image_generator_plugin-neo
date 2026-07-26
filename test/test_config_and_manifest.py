"""配置校验、manifest 与组件声明一致性测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.plugin import ImageGeneratorPlugin

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_manifest() -> dict:
    """读取插件 manifest。"""

    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_name_matches_plugin_class() -> None:
    """验证 manifest.name 与插件类 plugin_name 一致，否则无法加载。"""

    manifest = load_manifest()
    assert manifest["name"] == ImageGeneratorPlugin.plugin_name
    assert manifest["version"] == ImageGeneratorPlugin.plugin_version


def test_manifest_declares_all_components() -> None:
    """验证 manifest 组件声明数量与类型正确。"""

    manifest = load_manifest()
    declared = {
        (item["component_type"], item["component_name"]) for item in manifest["include"]
    }
    assert ("command", "nai_ref") in declared
    assert sum(kind == "action" for kind, _ in declared) == 8
    assert sum(kind == "command" for kind, _ in declared) == 4
    assert sum(kind == "service" for kind, _ in declared) == 1
    assert sum(kind == "router" for kind, _ in declared) == 1
    assert sum(kind == "config" for kind, _ in declared) == 1


def test_plugin_components_match_manifest() -> None:
    """验证全量启用时暴露的组件名与 manifest 声明一致。"""

    config = ImageGeneratorConfig()
    config.plugin.enabled = True
    config.webui.enabled = True
    config.components.director_bg_removal_enabled = True

    plugin = ImageGeneratorPlugin(config)
    exposed = {
        name
        for name in (getattr(component, "name", "") for component in plugin.get_components())
        if name
    }

    declared = {item["component_name"] for item in load_manifest()["include"]}
    assert declared - {"config"} == exposed
    assert ImageGeneratorConfig in plugin.configs


def test_disabled_plugin_exposes_no_components() -> None:
    """验证插件禁用时不注册任何组件。"""

    plugin = ImageGeneratorPlugin(ImageGeneratorConfig())
    assert plugin.get_components() == []


def test_config_rejects_invalid_values() -> None:
    """验证关键参数的取值范围。"""

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["generation"]["steps"] = 0
    with pytest.raises(ValidationError):
        ImageGeneratorConfig.model_validate(raw)

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["api"]["channel"] = "unknown"
    with pytest.raises(ValidationError):
        ImageGeneratorConfig.model_validate(raw)


def test_config_discards_empty_vibe_placeholders() -> None:
    """验证渲染器生成的空 Vibe 占位项不会阻断配置加载。"""

    raw = ImageGeneratorConfig().model_dump(mode="python")
    raw["vibe"]["always"] = [
        {"file": "", "enabled": True, "description": "", "ie": 1.0, "strength": 0.6}
    ]
    raw["vibe"]["selectable"] = [
        {"file": "   ", "enabled": True, "description": "", "ie": 1.0, "strength": 0.6},
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
    """验证列表默认值不在实例之间共享。"""

    first = ImageGeneratorConfig()
    second = ImageGeneratorConfig()
    first.api.api_keys.append("pst-example")
    assert second.api.api_keys == []
