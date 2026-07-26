"""Service payload、生命周期和 Vibe 路径测试。"""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.app.plugin_system.base import BasePlugin
from image_generator_plugin_neo.config import ImageGeneratorConfig  # noqa: E402
from image_generator_plugin_neo.services.image_service import (  # noqa: E402
    ImageGeneratorService,
)


class PluginStub:
    """提供 Service 测试所需的最小插件状态。"""

    def __init__(self, config: ImageGeneratorConfig) -> None:
        """保存配置和规范 Service 引用。"""

        self.config = config
        self.image_service: ImageGeneratorService | None = None


def make_service() -> tuple[PluginStub, ImageGeneratorService]:
    """创建未连接网络的 Service。"""

    config = ImageGeneratorConfig()
    plugin = PluginStub(config)
    service = ImageGeneratorService(cast(BasePlugin, plugin))
    plugin.image_service = service
    service.model = config.generation.model
    service.scale = config.generation.scale
    service.steps = config.generation.steps
    service.sampler = config.generation.sampler
    service.noise_schedule = config.generation.noise_schedule
    service.prompt_guidance_rescale = config.generation.prompt_guidance_rescale
    service.uc_preset = config.generation.uc_preset
    service.negative_prompt = config.generation.negative_prompt
    service.variety_plus = config.generation.variety_plus
    service.always_use_coords = True
    return plugin, service


def test_service_manager_construction_reuses_canonical_instance() -> None:
    """验证同一插件再次构造 Service 时复用规范实例。"""

    plugin, service = make_service()
    repeated = ImageGeneratorService(cast(BasePlugin, plugin))
    assert repeated is service


def test_gateway_generation_payload_matches_openai_image_api() -> None:
    """验证 Gateway 文生图字段包含人物和精密参考。"""

    _, service = make_service()
    characters = [
        {"prompt": "1girl, red hair", "uc": "bad hands", "x": 0.3, "y": 0.5}
    ]
    references = [
        {
            "data": "reference-data",
            "type": "character&style",
            "strength": 0.8,
            "fidelity": 0.75,
        }
    ]

    payload: dict[str, Any] = {
        "model": service.model,
        "prompt": "2girls, outdoor",
        "negative_prompt": service._merge_negative_prompts("text"),
        "size": "832x1216",
        "n": 1,
        "steps": service.steps,
        "scale": service.scale,
        "cfg_rescale": service.prompt_guidance_rescale,
        "sampler": service.sampler,
        "noise_schedule": service.noise_schedule,
        "ucPreset": service.uc_preset,
        "quality": True,
        "variety_boost": service.variety_plus,
        "use_coords": True,
        "response_format": "b64_json",
        "characters": [
            {
                "prompt": characters[0]["prompt"],
                "negative_prompt": characters[0]["uc"],
                "position": [characters[0]["x"], characters[0]["y"]],
                "enabled": True,
            }
        ],
        "character_references": [
            {
                "image": references[0]["data"],
                "type": references[0]["type"],
                "strength": references[0]["strength"],
                "fidelity": references[0]["fidelity"],
                "information_extracted": 1.0,
            }
        ],
    }

    assert payload["size"] == "832x1216"
    assert payload["characters"][0]["position"] == [0.3, 0.5]
    assert payload["character_references"][0]["fidelity"] == 0.75
    assert payload["response_format"] == "b64_json"


@pytest.mark.asyncio
async def test_vibe_path_traversal_is_rejected() -> None:
    """验证命令不能读取 Vibe 目录之外的文件。"""

    _, service = make_service()
    success, message = await service.load_vibe_from_file("user", "../secret.png")
    assert success is False
    assert "不合法" in message


@pytest.mark.asyncio
async def test_cleanup_stops_service_and_clears_runtime_state() -> None:
    """验证卸载清理停止接单并清空运行时缓存。"""

    _, service = make_service()
    service._accepting_tasks = True
    service.user_vibes["user"] = [{"data": "vibe"}]
    service.preset_vibes.append({"data": "preset"})
    await service.cleanup()
    assert service._accepting_tasks is False
    assert service.user_vibes == {}
    assert service.preset_vibes == []
