"""PNG 元数据处理与 WebUI 配置测试。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, PngImagePlugin

from image_generator_plugin_neo.config import ImageGeneratorConfig  # noqa: E402
from image_generator_plugin_neo.utils.image_utils import ImageUtils  # noqa: E402
from image_generator_plugin_neo.webui_logic import (  # noqa: E402
    apply_config_overrides,
    config_to_editor_payload,
)


def test_strip_metadata_quantizes_alpha_and_removes_text(tmp_path: Path) -> None:
    """验证透明度量化并移除 PNG 文本块。"""

    source = tmp_path / "source.png"
    image = Image.new("RGBA", (4, 1))
    image.putdata(
        [
            (255, 0, 0, 0),
            (255, 0, 0, 1),
            (255, 0, 0, 128),
            (255, 0, 0, 255),
        ]
    )
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "hidden prompt")
    image.save(source, pnginfo=metadata)

    stripped = ImageUtils.strip_png_metadata(str(source))
    with Image.open(io.BytesIO(stripped)) as output:
        alpha_channel = output.getchannel("A")
        alpha = [alpha_channel.getpixel((x, 0)) for x in range(output.width)]
        assert alpha == [0, 0, 136, 255]
        assert "Comment" not in output.info


def test_webui_payload_hides_api_keys() -> None:
    """验证 WebUI 编辑响应不会返回 NovelAI Token。"""

    config = ImageGeneratorConfig()
    config.api.api_keys = ["pst-secret"]
    payload = config_to_editor_payload(config, "config.toml")
    api_payload = payload["api"]
    assert "apiKeys" not in api_payload
    assert api_payload["apiKeyCount"] == 1
    assert "pst-secret" not in str(payload)


def test_webui_overrides_are_fully_validated() -> None:
    """验证 WebUI 覆盖后会通过完整配置模型校验。"""

    config = ImageGeneratorConfig()
    updated = apply_config_overrides(
        config,
        {
            "generation": {
                "steps": 30,
                "scale": 6.0,
                "resolution": "832x1216",
            }
        },
    )
    assert updated.generation.steps == 30
    assert updated.generation.scale == 6.0
    assert updated.generation.resolution == "832x1216"


def test_gateway_document_covers_used_endpoints() -> None:
    """验证插件 API 文档包含实际使用的 Gateway 端点。"""

    plugin_root = Path(__file__).resolve().parents[1]
    document = (plugin_root / "API_REQUEST_FORMAT.md").read_text(encoding="utf-8")
    expected: set[str] = {
        "/v1/images/generations",
        "/v1/images/img2img",
        "/v1/images/inpainting",
        "/v1/images/encode-vibe",
        "/v1/images/vibe-transfer",
        "/v1/images/director-declutter",
    }
    assert all(endpoint in document for endpoint in expected)


def test_payload_type_is_plain_dictionary() -> None:
    """保留一个轻量类型断言，避免编辑 payload 被替换为自定义对象。"""

    payload: dict[str, Any] = config_to_editor_payload(
        ImageGeneratorConfig(),
        "config.toml",
    )
    assert type(payload) is dict
