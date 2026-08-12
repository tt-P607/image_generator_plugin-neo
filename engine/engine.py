"""图片生成引擎。

编排配置快照、任务队列、HTTP 客户端、素材库与落盘，
对外提供文生图、图生图、局部重绘、导演工具四类能力。
两种渠道（official / gateway）的差异全部收敛在本模块内部。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from src.app.plugin_system.api.log_api import get_logger

from ..config import ImageGeneratorConfig
from ..media import image_ops
from . import assets as asset_lib
from . import payload as payload_builder
from . import storage
from .http import ApiRequestError, NovelAIHttpClient, RateLimitedError
from .queue import SerialTaskQueue
from .settings import EngineSettings
from .types import (
    DirectorToolSpec,
    GenerationSpec,
    ImageResult,
    InpaintSpec,
    UserVibeStore,
    VibeAsset,
)

logger = get_logger("image_generator_plugin.engine")

PLUGIN_NAME = "image_generator_plugin-neo"

DEFAULT_MANUAL_VIBE_IE = 1.0
DEFAULT_MANUAL_VIBE_STRENGTH = 0.6


class ImageEngine:
    """图片生成引擎。

    生命周期由插件掌控：``start()`` 后可接单，``close()`` 后释放全部资源。
    """

    def __init__(self, config: ImageGeneratorConfig) -> None:
        """初始化引擎。

        Args:
            config: 已校验的插件配置实例
        """
        self._config = config
        self._settings = EngineSettings.from_config(config)
        self._http = NovelAIHttpClient()
        self._queue = SerialTaskQueue(
            plugin_name=PLUGIN_NAME,
            cooldown=self._settings.cooldown,
        )
        self._assets = asset_lib.AssetLibrary()
        self._user_vibes = UserVibeStore()
        self._key_index = 0

    # ── 生命周期 ──

    @property
    def settings(self) -> EngineSettings:
        """当前生效的配置快照。"""

        return self._settings

    @property
    def assets(self) -> asset_lib.AssetLibrary:
        """素材库。"""

        return self._assets

    async def start(self) -> None:
        """准备目录、启动队列并加载素材。"""

        self._apply_settings()
        for directory in (
            self._settings.temp_dir,
            self._settings.command_images_dir,
            self._settings.vibe_storage_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if not self._settings.api_keys:
            logger.warning("未配置 API Key，图片生成功能将不可用")

        self._queue.start()
        await self._reload_assets()
        logger.info(f"图片生成引擎已就绪（渠道: {self._settings.channel}）")

    async def reload(self, config: ImageGeneratorConfig) -> None:
        """应用新配置并重新加载素材。

        Args:
            config: 新的插件配置实例
        """
        self._config = config
        self._settings = EngineSettings.from_config(config)
        self._apply_settings()
        await self._reload_assets()
        logger.info("图片生成引擎配置已刷新")

    async def close(self) -> None:
        """停止队列、关闭会话并清空缓存。"""

        await self._queue.shutdown()
        await self._http.close()
        self._assets.clear()
        self._user_vibes.clear_all()
        logger.info("图片生成引擎已关闭")

    def _apply_settings(self) -> None:
        """把配置快照同步到各子组件。"""

        self._http.set_proxy(self._settings.proxy)
        self._queue.set_cooldown(self._settings.cooldown)
        self._key_index = 0

    async def _reload_assets(self) -> None:
        """按当前配置重建素材池。"""

        await self._assets.reload(
            self._settings,
            always_items=self._config.vibe.always,
            selectable_items=self._config.vibe.selectable,
            director_items=(
                self._config.director_reference.selectable
                if self._config.director_reference.enabled
                else []
            ),
            encoder=self._encode_vibe,
        )

    # ── API Key ──

    def _current_key(self) -> str | None:
        """返回当前使用的 API Key。"""

        if not self._settings.api_keys:
            return None
        return self._settings.api_keys[self._key_index]

    def _rotate_key(self) -> None:
        """轮换到下一个 API Key。"""

        total = len(self._settings.api_keys)
        if total > 1:
            self._key_index = (self._key_index + 1) % total
            logger.info(f"切换到 API Key {self._key_index + 1}/{total}")

    # ── 公开能力 ──

    async def generate(self, spec: GenerationSpec) -> ImageResult:
        """执行文生图或图生图。

        Args:
            spec: 生图请求描述

        Returns:
            执行结果
        """
        if spec.characters:
            if not self._settings.is_v4_model:
                return ImageResult.failure(
                    f"当前模型 {self._settings.model!r} 不支持多人物生图（仅 V4 系列支持），"
                    "请先把 generation.model 切到 nai-diffusion-4-* 后再试"
                )
            if len(spec.characters) > self._settings.max_characters:
                return ImageResult.failure(
                    f"角色数量 {len(spec.characters)} 超过上限 "
                    f"{self._settings.max_characters}，"
                    "请合并角色或调高 advanced.max_characters 配置"
                )

        return await self._submit(lambda: self._run_generate(spec))

    async def inpaint(self, spec: InpaintSpec) -> ImageResult:
        """执行局部重绘。

        Args:
            spec: 局部重绘请求描述

        Returns:
            执行结果
        """
        return await self._submit(lambda: self._run_inpaint(spec))

    async def run_director_tool(self, spec: DirectorToolSpec) -> ImageResult:
        """执行导演工具处理。

        Args:
            spec: 导演工具请求描述

        Returns:
            执行结果
        """
        return await self._submit(lambda: self._run_director(spec))

    async def upscale(
        self,
        image_b64: str,
        *,
        from_command: bool = False,
    ) -> ImageResult:
        """执行 4x 图片放大。

        Args:
            image_b64: 源图 base64
            from_command: 结果是否保存到命令图片目录

        Returns:
            执行结果
        """
        return await self._submit(lambda: self._run_upscale(image_b64, from_command))

    async def get_user_info(self) -> tuple[bool, str]:
        """查询账号订阅信息（仅 official 渠道支持）。

        Returns:
            (是否成功, 面向用户的说明文本)
        """
        api_key = self._current_key()
        if not api_key:
            return False, "API Key 没配置，联系管理员看看"
        if self._settings.is_gateway:
            return False, "Gateway 渠道不支持账号信息查询"

        try:
            data = await self._http.get_json(
                self._settings.official_subscription_url,
                api_key,
            )
        except (RateLimitedError, ApiRequestError, aiohttp.ClientError) as error:
            return False, f"账号信息查询失败: {error}"
        except asyncio.TimeoutError:
            return False, "账号信息查询超时了"

        tier_names = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}
        tier = data.get("tier")
        active = data.get("active")
        tier_text = tier_names.get(tier, str(tier)) if tier is not None else "未知"
        lines = [
            f"订阅等级: {tier_text}",
            f"订阅状态: {'有效' if active else '已过期'}",
        ]
        training = data.get("trainingStepsLeft")
        if isinstance(training, dict):
            fixed = training.get("fixedTrainingStepsLeft", 0)
            purchased = training.get("purchasedTrainingSteps", 0)
            lines.append(f"Anlas: 固定 {fixed} / 购买 {purchased}")
        return True, "\n".join(lines)

    async def _submit(self, task: Callable[[], Awaitable[ImageResult]]) -> ImageResult:
        """把任务投入串行队列并统一兜底异常。

        Args:
            task: 无参异步任务

        Returns:
            执行结果
        """
        try:
            return await self._queue.submit(task)
        except RateLimitedError:
            self._rotate_key()
            return ImageResult.failure("请求太频繁了，等会儿再试吧")
        except ApiRequestError as error:
            return ImageResult.failure(f"请求失败了 ({error.status}): {error.detail}")
        except asyncio.TimeoutError:
            return ImageResult.failure("请求超时了，网络不太好")
        except aiohttp.ClientError as error:
            return ImageResult.failure(f"网络出问题了：{error}")
        except (OSError, ValueError) as error:
            logger.error(f"图片处理失败: {error}", exc_info=True)
            return ImageResult.failure(f"生成失败了：{error}")

    # ── 内部执行 ──

    async def _run_generate(self, spec: GenerationSpec) -> ImageResult:
        """在队列内执行一次生图。"""

        await self._queue.wait_for_cooldown()
        api_key = self._current_key()
        if not api_key:
            return ImageResult.failure("API Key 没配置，联系管理员看看")

        target_dir = self._settings.output_dir(spec.from_command)
        vibes = self._collect_vibes(spec)
        self._log_generation(spec, vibes)

        if self._settings.is_gateway:
            return await self._run_gateway_generate(spec, vibes, api_key, target_dir)

        prepared = self._prepare_img2img(spec)
        body = payload_builder.build_official_generation(
            self._settings,
            prepared,
            () if prepared.director_refs else vibes,
        )
        raw = await self._http.post_binary(
            self._settings.official_generate_url,
            body,
            api_key,
        )
        return ImageResult.ok(str(storage.save_response_payload(raw, target_dir)))

    def _log_generation(
        self,
        spec: GenerationSpec,
        vibes: tuple[VibeAsset, ...],
    ) -> None:
        """输出一次生图的完整可读摘要日志。

        按实际用到的内容组织：功能、画幅、模型与核心参数、正面/负面提示词、
        注入的 Vibe 与精密参考。只展示本次生效的项，避免冗余。

        Args:
            spec: 生图请求描述
            vibes: 本次实际注入的 Vibe（already 收集完成）
        """
        tags: list[str] = []
        if spec.is_img2img:
            tags.append("图生图")
        elif spec.characters:
            tags.append("多人物")
        else:
            tags.append("文生图")

        features: list[str] = []
        if spec.director_refs:
            features.append(
                "参考="
                + ", ".join(
                    f"{ref.ref_type}({ref.strength:.2f})" for ref in spec.director_refs
                )
            )
        if vibes:
            features.append(
                "Vibe="
                + ", ".join(
                    f"{vibe.name or '未命名'}(Str:{vibe.strength:.2f},IE:{vibe.information_extracted:.2f})"
                    for vibe in vibes
                )
            )
        if spec.characters:
            features.append(f"角色={len(spec.characters)}")

        params = (
            f"{spec.width}x{spec.height} | {self._settings.model} | "
            f"steps={self._settings.steps} | scale={spec.scale if spec.scale is not None else self._settings.scale} | "
            f"rescale={spec.cfg_rescale if spec.cfg_rescale is not None else self._settings.cfg_rescale} | "
            f"sampler={self._settings.sampler}"
        )

        lines = [
            f"生图开始 [{', '.join(tags)}]",
            f"  参数: {params}",
        ]
        if features:
            lines.append(f"  功能: {', '.join(features)}")
        lines.append(f"  正面: {spec.prompt}")
        if spec.negative_prompt:
            lines.append(f"  负面: {spec.negative_prompt}")
        logger.info("\n".join(lines))

    async def _run_gateway_generate(
        self,
        spec: GenerationSpec,
        vibes: tuple[VibeAsset, ...],
        api_key: str,
        target_dir: Path,
    ) -> ImageResult:
        """在 Gateway 渠道下执行文生图 / 图生图 / Vibe 转移。

        新版网关统一使用 ``/v1/images/generations`` 端点，根据请求体字段
        （image / reference_image_multiple）自动路由到对应功能。
        """

        url = self._settings.gateway_url(payload_builder.GATEWAY_GENERATIONS_PATH)
        body = payload_builder.build_gateway_generation(
            self._settings, spec, vibes
        )

        logger.info(f"[Gateway] POST {url} | {spec.width}x{spec.height}")
        response = await self._http.post_json(url, body, api_key)
        return await self._save_gateway_response(response, api_key, target_dir)

    async def _run_inpaint(self, spec: InpaintSpec) -> ImageResult:
        """在队列内执行一次局部重绘。"""

        await self._queue.wait_for_cooldown()
        api_key = self._current_key()
        if not api_key:
            return ImageResult.failure("API Key 没配置，联系管理员看看")

        target_dir = self._settings.output_dir(spec.from_command)
        logger.info(f"局部重绘 {spec.width}x{spec.height} | strength={spec.strength}")

        if self._settings.is_gateway:
            url = self._settings.gateway_url(payload_builder.GATEWAY_GENERATIONS_PATH)
            body = payload_builder.build_gateway_inpaint(self._settings, spec)
            response = await self._http.post_json(url, body, api_key)
            return await self._save_gateway_response(response, api_key, target_dir)

        body = payload_builder.build_official_inpaint(self._settings, spec)
        raw = await self._http.post_binary(
            self._settings.official_generate_url,
            body,
            api_key,
        )
        return ImageResult.ok(str(storage.save_response_payload(raw, target_dir)))

    async def _run_upscale(self, image_b64: str, from_command: bool) -> ImageResult:
        """在队列内执行一次 4x 放大。"""

        await self._queue.wait_for_cooldown()
        api_key = self._current_key()
        if not api_key:
            return ImageResult.failure("API Key 没配置，联系管理员看看")

        clean = image_ops.strip_data_url_prefix(image_b64)
        width, height = image_ops.read_image_size(clean)
        if not width or not height:
            return ImageResult.failure("无法读取图片尺寸")

        target_dir = self._settings.output_dir(from_command)
        logger.info(f"4x 放大 {width}x{height}")

        if self._settings.is_gateway:
            url = self._settings.gateway_url(payload_builder.GATEWAY_GENERATIONS_PATH)
            body = payload_builder.build_gateway_upscale(
                clean, width, height, self._settings.model
            )
            response = await self._http.post_json(url, body, api_key)
            return await self._save_gateway_response(response, api_key, target_dir)

        body = payload_builder.build_official_upscale(clean, width, height)
        raw = await self._http.post_binary(
            self._settings.official_upscale_url,
            body,
            api_key,
        )
        return ImageResult.ok(str(storage.save_response_payload(raw, target_dir)))

    async def _run_director(self, spec: DirectorToolSpec) -> ImageResult:
        """在队列内执行一次导演工具处理。"""

        await self._queue.wait_for_cooldown()
        api_key = self._current_key()
        if not api_key:
            return ImageResult.failure("API Key 没配置，联系管理员看看")

        target_dir = self._settings.output_dir(spec.from_command)
        logger.info(f"导演工具 {spec.tool_type} | {spec.width}x{spec.height}")

        if self._settings.is_gateway:
            url = self._settings.gateway_url(payload_builder.GATEWAY_GENERATIONS_PATH)
            body = payload_builder.build_gateway_director(spec, self._settings.model)
            response = await self._http.post_json(url, body, api_key)
            return await self._save_gateway_response(response, api_key, target_dir)

        body = payload_builder.build_official_director(spec)
        raw = await self._http.post_binary(
            self._settings.official_augment_url,
            body,
            api_key,
        )
        return ImageResult.ok(str(storage.save_response_payload(raw, target_dir)))

    def _prepare_img2img(self, spec: GenerationSpec) -> GenerationSpec:
        """official 渠道图生图时按需缩放原图到免费像素范围并对齐 64。

        Args:
            spec: 原始请求描述

        Returns:
            可能已替换原图与画幅的请求描述
        """
        if not spec.is_img2img or not spec.source_image:
            return spec
        if not self._settings.img2img_auto_downscale:
            return spec

        width, height = image_ops.read_image_size(spec.source_image)
        if not width or not height:
            return spec

        scaled, new_width, new_height = image_ops.downscale_to_free_tier(
            spec.source_image
        )
        if (new_width, new_height) == (width, height):
            return spec
        logger.info(f"图生图原图自动缩放: {width}x{height} → {new_width}x{new_height}")
        return replace_spec(spec, scaled, new_width, new_height)

    def _collect_vibes(self, spec: GenerationSpec) -> tuple[VibeAsset, ...]:
        """汇总本次生图需要注入的 Vibe。

        Args:
            spec: 生图请求描述

        Returns:
            always + LLM 自选 + 用户手动加载的 Vibe
        """
        collected: list[VibeAsset] = []
        if self._settings.vibe_always_enabled:
            collected.extend(self._assets.always_vibes)
        if self._settings.vibe_selectable_enabled and spec.selected_vibe_names:
            collected.extend(self._assets.select_vibes(spec.selected_vibe_names))
        collected.extend(self._user_vibes.get(spec.user_id))
        if collected:
            logger.info(
                "本次注入 Vibe: "
                + ", ".join(f"{asset.name or '未命名'}" for asset in collected)
            )
        return tuple(collected)

    async def _save_gateway_response(
        self,
        response: dict[str, Any],
        api_key: str,
        target_dir: Path,
    ) -> ImageResult:
        """保存 Gateway OpenAI 图片响应中的首张图片。

        Args:
            response: 已解析的响应对象
            api_key: 下载 URL 形式响应时使用的凭据
            target_dir: 保存目录

        Returns:
            执行结果
        """
        entries = response.get("data")
        if not isinstance(entries, list) or not entries:
            return ImageResult.failure("Gateway 响应中未找到图片数据")

        entry = entries[0]
        if not isinstance(entry, dict):
            return ImageResult.failure("Gateway 响应格式异常")

        b64_image = entry.get("b64_json")
        if isinstance(b64_image, str) and b64_image:
            return ImageResult.ok(
                str(storage.save_base64_image(b64_image, target_dir))
            )

        image_url = entry.get("url")
        if isinstance(image_url, str) and image_url:
            raw = await self._http.get_binary(image_url, api_key)
            return ImageResult.ok(str(storage.save_image_bytes(raw, target_dir)))

        return ImageResult.failure("Gateway 响应中未找到图片数据")

    async def _encode_vibe(
        self,
        image_b64: str,
        information_extracted: float,
    ) -> str | None:
        """调用 encode-vibe 端点把原图编码为可复用向量。

        Args:
            image_b64: 原始图片 base64
            information_extracted: 信息提取量

        Returns:
            编码向量 base64，失败时返回 None
        """
        api_key = self._current_key()
        if not api_key:
            logger.error("无 API Key，无法编码 Vibe")
            return None

        body = payload_builder.build_encode_vibe(
            self._settings,
            image_b64,
            information_extracted,
        )

        try:
            if self._settings.is_gateway:
                # 新版网关统一走 /v1/images/generations，通过 extra: "encode-vibe" 触发。
                # encode-vibe 请求体需要额外携带 extra 字段。
                body["extra"] = "encode-vibe"
                url = self._settings.gateway_url(
                    payload_builder.GATEWAY_GENERATIONS_PATH
                )
                response = await self._http.post_json(url, body, api_key, timeout=60)
                data = response.get("data")
                # 统一端点返回 {"data": [{"b64_json": "..."}]}
                if isinstance(data, list) and data:
                    entry = data[0]
                    if isinstance(entry, dict):
                        encoded = entry.get("b64_json")
                    else:
                        encoded = entry
                else:
                    encoded = data
                return encoded if isinstance(encoded, str) and encoded else None

            raw = await self._http.post_binary(
                self._settings.official_encode_vibe_url,
                body,
                api_key,
                accept="application/octet-stream",
                timeout=60,
            )
        except (RateLimitedError, ApiRequestError, aiohttp.ClientError) as error:
            logger.error(f"encode-vibe 失败: {error}")
            return None
        except asyncio.TimeoutError:
            logger.error("encode-vibe 超时")
            return None

        return base64.b64encode(raw).decode("utf-8")

    # ── 用户手动 Vibe ──

    def get_user_vibe_status(self, user_id: str) -> str:
        """返回用户当前已加载的 Vibe 概览。

        Args:
            user_id: 用户标识

        Returns:
            面向用户的描述文本
        """
        loaded = self._user_vibes.get(user_id)
        if not loaded:
            return "当前未加载任何 Vibe"

        lines = [f"当前已加载 {len(loaded)} 个 Vibe:"]
        lines.extend(
            f"{index}. {asset.name or '未命名'} | IE:{asset.information_extracted}, "
            f"Str:{asset.strength}"
            for index, asset in enumerate(loaded, start=1)
        )
        return "\n".join(lines)

    def clear_user_vibes(self, user_id: str) -> str:
        """清空用户手动加载的 Vibe。

        Args:
            user_id: 用户标识

        Returns:
            面向用户的描述文本
        """
        self._user_vibes.clear(user_id)
        return "已清空所有 Vibe 设置"

    def list_vibe_library(self) -> str:
        """列出素材库文件。

        Returns:
            面向用户的描述文本
        """
        files = asset_lib.list_vibe_files(self._settings.vibe_storage_dir)
        if not files:
            return "素材库为空"

        listing = "\n".join(f"• {name}" for name in files)
        return f"素材库文件列表:\n{listing}\n\n使用 /nai_vibe add [文件名] 加载素材"

    async def load_user_vibe(self, user_id: str, file_name: str) -> tuple[bool, str]:
        """从素材库加载一个 Vibe 到用户缓存。

        Args:
            user_id: 用户标识
            file_name: 素材文件名，支持模糊匹配

        Returns:
            (是否成功, 面向用户的说明)
        """
        storage_dir = self._settings.vibe_storage_dir
        resolved, message = asset_lib.resolve_vibe_file(storage_dir, file_name)
        if resolved is None:
            return False, message

        file_path = (storage_dir / resolved).resolve()
        if storage_dir.resolve() not in file_path.parents:
            return False, "Vibe 文件路径越界"

        try:
            source = asset_lib.read_source_image(file_path)
        except (OSError, ValueError) as error:
            return False, f"读取素材失败: {error}"
        if not source:
            return False, "文件数据无效或未找到 image 字段"

        vector = asset_lib.read_preencoded_vector(file_path, self._settings.model)
        if not vector:
            vector = await self._encode_vibe(source, DEFAULT_MANUAL_VIBE_IE)
        if not vector:
            return False, "Vibe 编码失败，请检查 API Key 和网络连接"

        asset = VibeAsset(
            data=vector,
            information_extracted=DEFAULT_MANUAL_VIBE_IE,
            strength=DEFAULT_MANUAL_VIBE_STRENGTH,
            name=Path(resolved).stem,
        )
        added, count = self._user_vibes.add(user_id, asset, self._settings.max_vibes)
        if not added:
            return False, f"最多同时叠加 {self._settings.max_vibes} 个 Vibe"

        return True, (
            f"已添加【{resolved}】\n"
            f"{count}. IE:{asset.information_extracted}, Str:{asset.strength}"
        )


def replace_spec(
    spec: GenerationSpec,
    source_image: str,
    width: int,
    height: int,
) -> GenerationSpec:
    """基于既有请求生成替换了原图与画幅的新请求。

    Args:
        spec: 原始请求描述
        source_image: 新的原图 base64
        width: 新宽度
        height: 新高度

    Returns:
        新的请求描述
    """
    return GenerationSpec(
        prompt=spec.prompt,
        user_id=spec.user_id,
        negative_prompt=spec.negative_prompt,
        width=width,
        height=height,
        scale=spec.scale,
        cfg_rescale=spec.cfg_rescale,
        source_image=source_image,
        strength=spec.strength,
        selected_vibe_names=spec.selected_vibe_names,
        director_refs=spec.director_refs,
        characters=spec.characters,
        from_command=spec.from_command,
    )
