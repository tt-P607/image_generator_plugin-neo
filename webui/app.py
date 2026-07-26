"""WebUI FastAPI 应用。

提供出图预览与配置编辑两类接口，静态页面由同目录 index.html 承载。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..engine import ImageEngine
from . import logic

if TYPE_CHECKING:
    from ..plugin import ImageGeneratorPlugin

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

INDEX_FILE = Path(__file__).with_name("index.html")


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


def create_app(
    *,
    plugin: "ImageGeneratorPlugin",
    config_path: str | Path = logic.DEFAULT_CONFIG_PATH,
) -> FastAPI:
    """创建 WebUI 应用。

    Args:
        plugin: 所属插件实例
        config_path: 插件配置文件路径

    Returns:
        FastAPI 应用实例
    """
    app = FastAPI(title="Image Generator WebUI", description="出图预览 + 配置编辑")
    resolved_path = Path(config_path)

    def require_engine() -> ImageEngine:
        """取出可用引擎，未就绪时返回 503。"""

        engine = plugin.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="图片生成服务尚未初始化")
        return engine

    @app.get("/")
    async def index() -> FileResponse:
        """返回 WebUI 主页面。"""

        return FileResponse(INDEX_FILE, headers=NO_CACHE_HEADERS)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """健康检查端点。"""

        return {"status": "ok", "service": "image_generator_webui"}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """返回已脱敏的可编辑配置。"""

        return logic.config_to_payload(plugin.image_config, resolved_path)

    @app.post("/api/config/save")
    async def save_config(request: ConfigSaveRequest) -> dict[str, Any]:
        """保存配置并刷新运行时。"""

        try:
            config = logic.save_config(request.overrides, resolved_path)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail="配置保存失败") from error

        await plugin.apply_config(config)
        return logic.config_to_payload(config, resolved_path)

    @app.post("/api/generate")
    async def generate(request: GenerateRequest) -> dict[str, Any]:
        """走生图队列出一张预览图。"""

        result = await logic.generate_preview(
            engine=require_engine(),
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            resolution=request.resolution,
            scale=request.scale,
            cfg_rescale=request.cfg_rescale,
            selected_vibes=request.selected_vibes,
        )
        if result["imageDataUrl"] is None:
            raise HTTPException(
                status_code=502,
                detail=result.get("error", "图片生成失败"),
            )
        return result

    return app
