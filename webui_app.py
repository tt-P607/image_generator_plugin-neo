"""WebUI FastAPI 应用。

提供两大功能：
- 出图预览（调用 ImageGeneratorService 走生图队列）
- 配置编辑（读写 config.toml 常用字段）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .webui_logic import (
    DEFAULT_PLUGIN_CONFIG_PATH,
    config_to_editor_payload,
    generate_preview_image,
    initialize_webui_runtime,
    load_plugin_config,
    save_plugin_config,
)

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

    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    resolution: str = "832x1216"
    scale: float | None = None
    cfg_rescale: float | None = None
    selected_vibes: list[str] | None = None


class ConfigSaveRequest(BaseModel):
    """配置保存请求体。"""

    overrides: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
#  应用工厂
# ═══════════════════════════════════════════════════════════════════════


def create_app(
    *,
    config_path: str | Path = DEFAULT_PLUGIN_CONFIG_PATH,
    initialize_runtime: bool = True,
) -> FastAPI:
    """创建 WebUI 应用。

    Args:
        config_path: 插件配置文件路径
        initialize_runtime: 是否在启动时初始化运行时配置

    Returns:
        FastAPI 应用实例
    """
    app = FastAPI(
        title="Image Generator WebUI",
        description="出图预览 + 配置编辑",
    )
    app.state.config_path = Path(config_path)

    if initialize_runtime:
        initialize_webui_runtime()

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
        """返回完整配置信息供前端编辑。"""
        config = load_plugin_config(app.state.config_path)
        return config_to_editor_payload(config, app.state.config_path)

    # ── 配置保存 ──

    @app.post("/api/config/save")
    async def api_save_config(request: ConfigSaveRequest) -> dict[str, Any]:
        """保存配置到 TOML 文件。"""
        try:
            config = save_plugin_config(request.overrides, app.state.config_path)
            return config_to_editor_payload(config, app.state.config_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ── 出图预览 ──

    @app.post("/api/generate")
    async def api_generate(request: GenerateRequest) -> dict[str, Any]:
        """调用生图队列出一张预览图。"""
        result = await generate_preview_image(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            resolution=request.resolution,
            scale=request.scale,
            cfg_rescale=request.cfg_rescale,
            selected_vibes=request.selected_vibes,
            config_path=app.state.config_path,
        )
        if result.get("imageDataUrl") is None:
            raise HTTPException(
                status_code=502,
                detail=result.get("error", "图片生成失败"),
            )
        return result

    return app
