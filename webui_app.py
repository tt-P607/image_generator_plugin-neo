"""WebUI FastAPI 应用。

提供两大功能：
- 出图预览（调用 ImageGeneratorService 走生图队列）
- 配置编辑（读写 config.toml 常用字段）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import ImageGeneratorConfig
from .services.image_service import ImageGeneratorService
from .webui_logic import (
    DEFAULT_PLUGIN_CONFIG_PATH,
    config_to_editor_payload,
    generate_preview_image,
    save_plugin_config,
)

class ImageGeneratorPluginProtocol(Protocol):
    """WebUI 所需的最小插件接口。"""

    config: ImageGeneratorConfig | None
    image_service: ImageGeneratorService | None

    async def refresh_runtime_config(self, config: ImageGeneratorConfig) -> None:
        """应用并刷新运行时配置。"""

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# ═══════════════════════════════════════════════════════════════════════
#  请求体模型
# ═══════════════════════════════════════════════════════════════════════


class GenerateRequest(BaseModel):
    """出图预览请求体。"""

    prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=20000)
    resolution: str = Field(default="832x1216", pattern=r"^\d{2,5}x\d{2,5}$")
    scale: float | None = Field(default=None, ge=1.0, le=10.0)
    cfg_rescale: float | None = Field(default=None, ge=0.0, le=1.0)
    selected_vibes: list[str] | None = Field(default=None, max_length=16)


class ConfigSaveRequest(BaseModel):
    """配置保存请求体。"""

    overrides: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
#  应用工厂
# ═══════════════════════════════════════════════════════════════════════


def create_app(
    *,
    plugin: ImageGeneratorPluginProtocol,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
) -> FastAPI:
    """创建 WebUI 应用。

    Args:
        plugin: 当前图片生成插件实例
        config_path: 插件配置文件路径

    Returns:
        FastAPI 应用实例
    """
    app = FastAPI(
        title="Image Generator WebUI",
        description="出图预览 + 配置编辑",
    )
    app.state.config_path = Path(config_path)
    app.state.plugin = plugin

    def require_service() -> ImageGeneratorService:
        """返回插件持有的规范 Service 实例。"""

        service = cast(ImageGeneratorService | None, plugin.image_service)
        if service is None:
            raise HTTPException(status_code=503, detail="图片生成服务尚未初始化")
        return service

    # ── 静态页面 ──

    @app.get("/")
    async def index() -> FileResponse:
        """返回 WebUI 主页面。"""
        return FileResponse(
            Path(__file__).with_name("webui") / "index.html",
            headers=NO_CACHE_HEADERS,
        )

    # ── 健康检查 / 配置读取 ──

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """健康检查端点。"""
        return {"status": "ok", "service": "image_generator_webui"}

    @app.get("/api/config")
    async def api_get_config() -> dict[str, Any]:
        """返回已脱敏的可编辑配置。"""

        config = cast(ImageGeneratorConfig, plugin.config)
        return config_to_editor_payload(config, app.state.config_path)

    # ── 配置保存 ──

    @app.post("/api/config/save")
    async def api_save_config(request: ConfigSaveRequest) -> dict[str, Any]:
        """保存配置到 TOML 文件。"""
        try:
            config = save_plugin_config(request.overrides, app.state.config_path)
            await plugin.refresh_runtime_config(config)
            return config_to_editor_payload(config, app.state.config_path)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail="配置保存失败") from error

    # ── 出图预览 ──

    @app.post("/api/generate")
    async def api_generate(request: GenerateRequest) -> dict[str, Any]:
        """调用生图队列出一张预览图。"""
        result = await generate_preview_image(
            service=require_service(),
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            resolution=request.resolution,
            scale=request.scale,
            cfg_rescale=request.cfg_rescale,
            selected_vibes=request.selected_vibes,
        )
        if result.get("imageDataUrl") is None:
            raise HTTPException(
                status_code=502,
                detail=result.get("error", "图片生成失败"),
            )
        return result

    return app
