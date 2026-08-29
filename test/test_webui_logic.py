"""WebUI 配置读写与安全性测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from image_generator_plugin_neo.config import ImageGeneratorConfig
from image_generator_plugin_neo.engine.engine import ImageEngine
from image_generator_plugin_neo.engine.settings import EngineSettings
from image_generator_plugin_neo.engine.types import GenerationSpec, ImageResult
from image_generator_plugin_neo.webui import logic

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _PreviewEngine:
    """记录 WebUI 预览请求的测试引擎。"""

    def __init__(self, image_path: Path) -> None:
        """初始化测试引擎与产图路径。"""

        self.settings = EngineSettings.from_config(ImageGeneratorConfig())
        self.image_path = image_path
        self.spec: GenerationSpec | None = None

    async def generate(self, spec: GenerationSpec) -> ImageResult:
        """记录请求并返回预置图片。"""

        self.spec = spec
        return ImageResult.ok(str(self.image_path))


def test_payload_never_exposes_api_keys() -> None:
    """验证编辑载荷不会泄露 NovelAI Token。"""

    config = ImageGeneratorConfig()
    config.api.api_keys = ["pst-secret"]

    payload = logic.config_to_payload(config, "config.toml")
    assert "apiKeys" not in payload["api"]
    assert payload["api"]["apiKeyCount"] == 1
    assert "pst-secret" not in str(payload)


def test_overrides_are_fully_validated() -> None:
    """验证覆盖字段后会重新走完整配置校验。"""

    updated = logic.apply_overrides(
        ImageGeneratorConfig(),
        {"generation": {"steps": 30, "scale": 6.0, "resolution": "832x1216"}},
    )
    assert updated.generation.steps == 30
    assert updated.generation.scale == 6.0
    assert updated.generation.resolution == "832x1216"


def test_payload_and_overrides_include_model_whitelist() -> None:
    """验证 WebUI 可读取和保存严格模型白名单。"""

    config = ImageGeneratorConfig()
    config.generation.available_models = [
        "nai-diffusion-5-curated",
        "nai-diffusion-4-5-full",
    ]
    payload = logic.config_to_payload(config, "config.toml")
    assert payload["generation"]["availableModels"] == [
        "nai-diffusion-5-curated",
        "nai-diffusion-4-5-full",
    ]

    updated = logic.apply_overrides(
        ImageGeneratorConfig(),
        {
            "generation": {
                "model": "nai-diffusion-4-5-full",
                "availableModels": [
                    "nai-diffusion-4-5-full",
                    "nai-diffusion-5-full",
                ],
            }
        },
    )
    assert updated.generation.model == "nai-diffusion-4-5-full"
    assert updated.generation.available_models == [
        "nai-diffusion-4-5-full",
        "nai-diffusion-5-full",
    ]


def test_overrides_ignore_unknown_fields() -> None:
    """验证白名单之外的字段不会被写入配置。"""

    updated = logic.apply_overrides(
        ImageGeneratorConfig(),
        {"api": {"apiKeys": ["pst-injected"]}, "generation": {"steps": 20}},
    )
    assert updated.api.api_keys == []
    assert updated.generation.steps == 20


def test_overrides_drop_empty_list_entries() -> None:
    """验证缺少文件名的 Vibe 与参考项会被丢弃。"""

    updated = logic.apply_overrides(
        ImageGeneratorConfig(),
        {
            "vibe": {
                "selectable": [
                    {"file": ""},
                    {"file": "style.naiv4vibe", "ie": 0.8, "strength": 0.5},
                ]
            },
            "directorReference": {"selectable": [{"file": ""}]},
        },
    )
    assert len(updated.vibe.selectable) == 1
    assert updated.vibe.selectable[0].file == "style.naiv4vibe"
    assert updated.director_reference.selectable == []


def test_parse_resolution_falls_back_on_invalid_input() -> None:
    """验证非法画幅回退到默认竖图。"""

    assert logic.parse_resolution("1216x832") == (1216, 832)
    assert logic.parse_resolution("bad") == logic.DEFAULT_PREVIEW_RESOLUTION


async def test_generate_preview_passes_model_specific_options(
    tmp_path: Path,
) -> None:
    """验证 WebUI 预览把调用级模型参数完整传给引擎。"""

    image_path = tmp_path / "preview.png"
    image_path.write_bytes(b"png")
    engine = _PreviewEngine(image_path)

    payload = await logic.generate_preview(
        engine=cast(ImageEngine, engine),
        prompt='girl holding a sign reading "Hello"',
        model="nai-diffusion-5-full",
        steps=24,
        variety_plus=True,
        render_text=True,
    )

    assert engine.spec is not None
    assert engine.spec.model == "nai-diffusion-5-full"
    assert engine.spec.steps == 24
    assert engine.spec.variety_plus is True
    assert engine.spec.render_text is True
    assert payload["actualModel"] == "nai-diffusion-5-full"
    assert payload["actualSteps"] == 24


def test_gateway_document_covers_used_endpoints() -> None:
    """验证 API 文档覆盖插件实际调用的 Gateway 端点与 extra 路由值。"""

    document = (PLUGIN_ROOT / "API_REQUEST_FORMAT.md").read_text(encoding="utf-8")
    expected = {
        "/v1/images/generations",
        "extra",
        "upscale",
        "encode-vibe",
        "director-colorize",
        "qualityToggle",
    }
    assert all(token in document for token in expected)
