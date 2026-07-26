"""图片生成服务。

处理与 NovelAI 官方 API 的交互逻辑，包含防 429 封号的任务队列机制、
Vibe 参考图传递等核心功能。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, cast

import aiohttp
from PIL import Image

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, BaseService
from src.kernel.concurrency import get_task_manager

if TYPE_CHECKING:
    from ..config import ImageGeneratorConfig

logger = get_logger("image_generator_plugin.service")


class ImageGeneratorService(BaseService):
    """图片生成服务。

    负责与 NovelAI 官方 API 交互，处理图片生成、Vibe 参考图传递等功能。
    使用实例级任务队列串行化所有生图请求，防止 429 封号。
    """

    name: str = "image_generator"
    description: str = "NovelAI 图片生成服务"

    def __new__(cls, plugin: BasePlugin) -> "ImageGeneratorService":
        """让框架新建的 Service 实例复用插件持有的规范实例。"""

        existing = getattr(plugin, "image_service", None)
        if isinstance(existing, cls):
            return existing
        return super().__new__(cls)

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化服务。

        Args:
            plugin: 所属插件实例
        """
        if getattr(self, "_constructed", False):
            return
        super().__init__(plugin)
        self._constructed = True

        # 插件目录（通过 __file__ 推算）
        self.plugin_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # 运行时状态
        self._task_queue: asyncio.Queue[
            tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]
        ] = asyncio.Queue()
        self._queue_worker_task_id: str | None = None
        self._accepting_tasks: bool = False
        self._http_session: aiohttp.ClientSession | None = None
        self.current_key_index: int = 0
        self.last_request_time: float = 0
        self.user_vibes: dict[str, list[dict[str, Any]]] = {}
        self.preset_vibes: list[dict[str, Any]] = []  # 启动时编码好的预设 Vibe
        self.selectable_vibes: dict[str, dict[str, Any]] = {}  # 按名称索引的可选 Vibe 池
        self.manual_vibe_enabled: bool = False
        self.auto_vibe_select: bool = False

        # 配置将在 initialize() 中加载
        self.channel: str = "official"  # "official" 或 "gateway"
        self.api_keys: list[str] = []
        self.base_url: str = ""
        self.gateway_base_url: str = ""  # 规范化后的 gateway 根地址（从 base_url 推导）
        self.api_base_url: str = "https://api.novelai.net"  # official 渠道 API 域名（upscale 等端点）
        self.proxy: str = ""
        self.cooldown: int = 20
        self.model: str = ""
        self.noise_schedule: str = ""
        self.resolution: str = ""
        self.steps: int = 28
        self.scale: float = 5.0
        self.sampler: str = ""
        self.prompt_guidance_rescale: float = 0.0
        self.uc_preset: int = 0
        self.variety_plus: bool = False
        self.negative_prompt: str = ""
        self.character_prompt: str = ""
        self.max_vibes: int = 4
        self.img2img_default_strength: float = 0.7
        self.always_use_coords: bool = True
        self.max_characters: int = 6
        self.img2img_auto_downscale: bool = True
        self.temp_dir: Path = Path()
        self.vibe_storage_dir: Path = Path()
        self.command_images_dir: Path = Path()
        self.selectable_director_refs: dict[str, dict[str, Any]] = {}  # 精密参考池

    # ═════════════════════════════════════════════════════════════════════
    #  初始化 / 清理
    # ═════════════════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        """初始化服务：加载配置、创建目录、HTTP 会话和队列处理器。"""
        cfg = cast("ImageGeneratorConfig", self.plugin.config)

        # API 配置
        self.channel = cfg.api.channel
        self.api_keys = list(cfg.api.api_keys)
        self.base_url = cfg.api.base_url
        # 规范化 gateway 根地址：从 base_url 推导，去掉末尾的 /v1 和 /
        _gw_url = cfg.api.base_url.rstrip("/")
        if _gw_url.endswith("/v1"):
            _gw_url = _gw_url[:-3]
        self.gateway_base_url = _gw_url
        self.proxy = cfg.api.proxy
        self.cooldown = cfg.api.cooldown
        self.api_base_url = cfg.api.api_base_url
        logger.info(f"生图渠道: {self.channel}")

        # 生图参数
        self.model = cfg.generation.model
        self.noise_schedule = cfg.generation.noise_schedule
        self.resolution = cfg.generation.resolution
        self.steps = cfg.generation.steps
        self.scale = cfg.generation.scale
        self.sampler = cfg.generation.sampler
        self.prompt_guidance_rescale = cfg.generation.prompt_guidance_rescale
        self.uc_preset = cfg.generation.uc_preset
        self.variety_plus = cfg.generation.variety_plus
        self.negative_prompt = cfg.generation.negative_prompt
        self.character_prompt = cfg.generation.character_prompt
        self.always_use_coords = cfg.generation.always_use_coords
        self.img2img_auto_downscale = cfg.generation.img2img_auto_downscale

        # 高级参数
        self.max_vibes = cfg.advanced.max_vibes
        self.img2img_default_strength = cfg.advanced.img2img_default_strength
        self.max_characters = cfg.advanced.max_characters

        # 目录
        self.temp_dir = Path(cfg.advanced.temp_dir).absolute()
        self.vibe_storage_dir = Path(cfg.advanced.vibe_storage_dir).absolute()
        self.command_images_dir = Path(cfg.advanced.command_images_dir).absolute()

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.vibe_storage_dir.mkdir(parents=True, exist_ok=True)
        self.command_images_dir.mkdir(parents=True, exist_ok=True)

        # Vibe 配置
        self.always_inject = cfg.vibe.always_enabled
        self.selectable_enabled = cfg.vibe.selectable_enabled

        # 检查配置
        if not self.api_keys:
            logger.warning("未配置 API Key，图片生成功能将不可用")

        await self._get_http_session()
        self._accepting_tasks = True
        await self._start_queue_worker()

        # 加载并编码 always Vibe
        if cfg.vibe.always_enabled and cfg.vibe.always and self.api_keys:
            enabled_always = [v for v in cfg.vibe.always if v.enabled and v.file.strip()]
            await self._load_preset_vibes(enabled_always)

        # 加载并编码 selectable Vibe 池（不过滤 enabled，LLM 始终可选）
        if cfg.vibe.selectable and self.api_keys:
            await self._load_selectable_vibes(cfg.vibe.selectable)

        # 加载精密参考池
        if cfg.director_reference.selectable and self.api_keys:
            await self._load_selectable_director_refs(cfg.director_reference.selectable)

        logger.info(f"精密参考池加载完成，共 {len(self.selectable_director_refs)} 个")
        logger.info("图片生成服务初始化完成")

    async def cleanup(self) -> None:
        """停止接单、取消队列 worker、结束等待者并关闭 HTTP 会话。"""

        self._accepting_tasks = False
        if self._queue_worker_task_id is not None:
            get_task_manager().cancel_task(self._queue_worker_task_id)
            self._queue_worker_task_id = None

        while not self._task_queue.empty():
            try:
                _, future = self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(RuntimeError("图片生成插件正在卸载"))
            self._task_queue.task_done()

        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None
        self.user_vibes.clear()
        self.preset_vibes.clear()
        self.selectable_vibes.clear()
        self.selectable_director_refs.clear()

    async def refresh_config(self) -> None:
        """重新加载配置及其关联的 Vibe 和精密参考素材。"""

        self.preset_vibes.clear()
        self.selectable_vibes.clear()
        self.selectable_director_refs.clear()
        await self.initialize()

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """获取插件生命周期内复用的 HTTP 会话。"""

        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=8),
                timeout=aiohttp.ClientTimeout(total=120),
            )
        return self._http_session

    def _request_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        """构造统一请求参数，并按配置附加 HTTP 代理。"""

        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    # ═════════════════════════════════════════════════════════════════════
    #  任务队列（防 429 封号核心机制）
    # ═════════════════════════════════════════════════════════════════════

    async def _start_queue_worker(self) -> None:
        """启动插件持有的串行任务队列处理器。"""

        if self._queue_worker_task_id is not None:
            task_info = get_task_manager().get_task(self._queue_worker_task_id)
            if not task_info.is_done():
                return
            self._queue_worker_task_id = None

        task_info = get_task_manager().create_task(
            self._queue_worker(),
            name="image_generator_queue_worker",
            daemon=True,
            metadata={
                "plugin": "image_generator_plugin-neo",
                "purpose": "generation_queue_worker",
            },
        )
        self._queue_worker_task_id = task_info.task_id
        logger.info("生图串行任务队列处理器已启动（防 429 封号）")

    async def _load_selectable_vibes(self, selectable: list[Any]) -> None:
        """在初始化时加载并编码所有可选 Vibe，结果按名称缓存在 self.selectable_vibes。"""
        self.selectable_vibes = {}
        for item in selectable:
            if not item.enabled or not item.file.strip():
                continue
            file_path = self.vibe_storage_dir / item.file
            if not file_path.exists():
                logger.warning(f"可选 Vibe 文件不存在，跳过: {item.file}")
                continue
            try:
                # 优先读取文件内预编码向量，避免消耗 Anlas
                preencoded = self._try_get_preencoded_vibe(file_path)
                if preencoded:
                    name = Path(item.file).stem
                    self.selectable_vibes[name] = {
                        "data": preencoded,
                        "ie": item.ie,
                        "strength": item.strength,
                    }
                    logger.info(f"已加载预编码可选 Vibe（不消耗 Anlas）: {name} ({item.file})")
                    continue
                raw_b64 = self._read_image_b64_from_vibe_file(file_path)
                if not raw_b64:
                    logger.warning(f"可选 Vibe 文件读取为空，跳过: {item.file}")
                    continue
                encoded = await self._encode_vibe(raw_b64, item.ie)
                if not encoded:
                    logger.warning(f"可选 Vibe 编码失败，跳过: {item.file}")
                    continue
                name = Path(item.file).stem
                self.selectable_vibes[name] = {
                    "data": encoded,
                    "ie": item.ie,
                    "strength": item.strength,
                }
                logger.info(f"已编码可选 Vibe: {name} ({item.file})")
            except Exception as e:
                logger.error(f"加载可选 Vibe 失败 [{item.file}]: {e}")
        logger.info(f"可选 Vibe 池加载完成，共 {len(self.selectable_vibes)} 个")

    async def _load_selectable_director_refs(self, selectable: list[Any]) -> None:
        """在初始化时加载所有可选精密参考图。

        NAI API 要求精密参考图必须是 1024x1536 的 PNG（保持宽高比，黑色填充）。
        """
        self.selectable_director_refs = {}
        for item in selectable:
            if not getattr(item, "enabled", True):
                continue
            file_path = self.vibe_storage_dir / item.file
            if not file_path.exists():
                logger.warning(f"精密参考文件不存在，跳过: {item.file}")
                continue
            try:
                raw_b64 = self._read_image_b64_from_vibe_file(file_path)
                if not raw_b64:
                    continue
                # NAI API 要求精密参考图为 1024x1536 PNG，需要先 crop_and_resize
                processed_b64 = self._crop_and_resize_for_director_ref(raw_b64)
                name = item.name or Path(item.file).stem
                self.selectable_director_refs[name] = {
                    "data": processed_b64,
                    "type": item.type,
                    "fidelity": item.fidelity,
                    "strength": item.strength,
                }
                logger.info(f"已加载精密参考: {name} ({item.file})")
            except Exception as e:
                logger.error(f"加载精密参考失败 [{item.file}]: {e}")
        logger.info(f"精密参考池加载完成，共 {len(self.selectable_director_refs)} 个")

    def _crop_and_resize_for_director_ref(self, raw_b64: str) -> str:
        """将图片缩放并填充到 1024x1536（NAI 精密参考图要求的格式）。

        保持宽高比，不足部分用黑色填充，输出 PNG 格式。

        Args:
            raw_b64: 原始图片 base64 字符串

        Returns:
            处理后的 base64 字符串（1024x1536 PNG）
        """
        import io
        try:
            img_data = base64.b64decode(raw_b64)
            img = Image.open(io.BytesIO(img_data)).convert("RGB")

            target_w, target_h = 1024, 1536
            src_w, src_h = img.size

            src_ratio = src_w / src_h
            target_ratio = target_w / target_h

            if src_ratio > target_ratio:
                new_w = target_w
                new_h = int(target_w / src_ratio)
            else:
                new_h = target_h
                new_w = int(target_h * src_ratio)

            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            background = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            offset_x = (target_w - new_w) // 2
            offset_y = (target_h - new_h) // 2
            background.paste(resized, (offset_x, offset_y))

            buf = io.BytesIO()
            background.save(buf, format="PNG")
            result_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            logger.debug(
                f"精密参考图已处理: {src_w}x{src_h} → {target_w}x{target_h} "
                f"({len(raw_b64)//1024}KB → {len(result_b64)//1024}KB)"
            )
            return result_b64
        except Exception as e:
            logger.warning(f"精密参考图处理失败，使用原图: {e}")
            return raw_b64

    async def _load_preset_vibes(self, presets: list[Any]) -> None:
        """在初始化时加载并编码所有预设 Vibe，结果缓存在 self.preset_vibes。"""
        self.preset_vibes = []
        for preset in presets:
            if not preset.enabled or not preset.file.strip():
                continue
            file_path = self.vibe_storage_dir / preset.file
            if not file_path.exists():
                logger.warning(f"预设 Vibe 文件不存在，跳过: {preset.file}")
                continue
            try:
                # 优先读取文件内预编码向量，避免消耗 Anlas
                preencoded = self._try_get_preencoded_vibe(file_path)
                if preencoded:
                    self.preset_vibes.append({
                        "data": preencoded,
                        "ie": preset.ie,
                        "strength": preset.strength,
                    })
                    logger.info(f"已加载预编码预设 Vibe（不消耗 Anlas）: {preset.file}")
                    continue
                raw_b64 = self._read_image_b64_from_vibe_file(file_path)
                if not raw_b64:
                    logger.warning(f"预设 Vibe 文件读取为空，跳过: {preset.file}")
                    continue
                encoded = await self._encode_vibe(raw_b64, preset.ie)
                if not encoded:
                    logger.warning(f"预设 Vibe 编码失败，跳过: {preset.file}")
                    continue
                self.preset_vibes.append({
                    "data": encoded,
                    "ie": preset.ie,
                    "strength": preset.strength,
                })
                logger.info(f"已编码预设 Vibe: {preset.file} (IE={preset.ie}, Str={preset.strength})")
            except Exception as e:
                logger.error(f"加载预设 Vibe 失败 [{preset.file}]: {e}")
        logger.info(f"预设 Vibe 加载完成，共 {len(self.preset_vibes)} 个")

    async def _queue_worker(self) -> None:
        """串行处理所有生图和编辑任务。"""

        try:
            while True:
                task_func, result_future = await self._task_queue.get()
                try:
                    result = await task_func()
                    if not result_future.done():
                        result_future.set_result(result)
                except asyncio.CancelledError:
                    if not result_future.done():
                        result_future.set_exception(RuntimeError("图片生成任务已取消"))
                    raise
                except Exception as error:
                    logger.error(f"队列任务异常: {error}", exc_info=True)
                    if not result_future.done():
                        result_future.set_exception(error)
                finally:
                    self._task_queue.task_done()
        finally:
            self._queue_worker_task_id = None
            logger.info("生图队列处理器已退出")

    async def _enqueue_task(
        self,
        task_func: Callable[[], Awaitable[Any]],
    ) -> Any:
        """将任务加入队列并等待结果。

        Args:
            task_func: 异步任务函数

        Returns:
            任务执行结果
        """
        if not self._accepting_tasks:
            raise RuntimeError("图片生成服务尚未初始化或正在卸载")
        if self._queue_worker_task_id is None:
            await self._start_queue_worker()
        result_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._task_queue.put((task_func, result_future))
        logger.info(f"任务已加入队列，当前队列长度: {self._task_queue.qsize()}")
        return await result_future

    # ═════════════════════════════════════════════════════════════════════
    #  API Key 管理
    # ═════════════════════════════════════════════════════════════════════

    def _get_current_api_key(self) -> Optional[str]:
        """获取当前 API Key。"""
        if not self.api_keys:
            return None
        return self.api_keys[self.current_key_index]

    def _rotate_api_key(self) -> None:
        """轮换 API Key。"""
        if len(self.api_keys) > 1:
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            logger.info(f"切换到 API Key {self.current_key_index + 1}/{len(self.api_keys)}")

    def _try_get_preencoded_vibe(self, file_path: Path) -> Optional[str]:
        """尝试从 .naiv4vibe / .naiv4vibebundle 文件中提取预编码好的 Vibe 向量。

        NAI 导出的 .naiv4vibe 文件在 encodings 字段中已含有按模型分组的预编码向量，
        直接读取可避免调用 /ai/encode-vibe 接口，不消耗 Anlas。

        Args:
            file_path: vibe 文件路径

        Returns:
            预编码的 Vibe base64 字符串，不存在则返回 None
        """
        suffix = file_path.suffix.lower()
        if suffix not in (".naiv4vibe", ".naiv4vibebundle"):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            encodings: dict[str, Any] = data.get("encodings", {})
            if not encodings:
                return None
            # 当前模型对应的编码键映射
            _MODEL_KEY_MAP: dict[str, str] = {
                "nai-diffusion-4-5": "v4-5full",
                "nai-diffusion-4-5-inpainting": "v4-5full",
                "nai-diffusion-4-curated-preview": "v4",
                "nai-diffusion-4-full": "v4full",
                "nai-diffusion-3": "v3",
            }
            key = _MODEL_KEY_MAP.get(self.model)
            model_encodings: dict[str, Any] | None = None
            if key and key in encodings:
                model_encodings = encodings[key]
            elif encodings:
                # 取第一个可用的模型编码
                model_encodings = next(iter(encodings.values()))
            if not model_encodings:
                return None
            # 取第一个 hash 条目下的 encoding 字段
            first_entry: dict[str, Any] = next(iter(model_encodings.values()))
            return first_entry.get("encoding")
        except Exception as e:
            logger.warning(f"读取预编码 Vibe 失败 [{file_path.name}]: {e}")
            return None

    async def _encode_vibe(self, raw_image_b64: str, information_extracted: float) -> Optional[str]:
        """按当前渠道将原始图片编码为可复用的 Vibe 数据。

        Args:
            raw_image_b64: 原始图片的 base64 字符串（PNG/JPG）
            information_extracted: 信息提取量（0.0–1.0）

        Returns:
            编码后的 Vibe 数据 base64 字符串，失败返回 None
        """
        api_key = self._get_current_api_key()
        if not api_key:
            logger.error("无 API Key，无法编码 Vibe")
            return None

        if self.channel == "gateway":
            encode_url = f"{self.gateway_base_url}/v1/images/encode-vibe"
        else:
            encode_url = self.base_url.replace("/ai/generate-image", "/ai/encode-vibe")

        payload = {
            "image": raw_image_b64,
            "information_extracted": information_extracted,
            "model": self.model,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Origin": "https://novelai.net",
            "Referer": "https://novelai.net",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
        }
        try:
            session = await self._get_http_session()
            request_kwargs: dict[str, Any] = {
                "json": payload,
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=60),
            }
            if self.proxy:
                request_kwargs["proxy"] = self.proxy

            async with session.post(encode_url, **request_kwargs) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    logger.error(f"encode-vibe 请求失败 ({resp.status}): {err[:500]}")
                    return None
                if self.channel == "gateway":
                    data = await resp.json()
                    encoded = data.get("data")
                    if not isinstance(encoded, str) or not encoded:
                        logger.error("Gateway encode-vibe 响应缺少 data 字段")
                        return None
                    return encoded
                content = await resp.read()
                return base64.b64encode(content).decode("utf-8")
        except Exception as e:
            logger.error(f"encode-vibe 异常: {e}")
            return None

    def _read_image_b64_from_vibe_file(self, file_path: Path) -> str:
        """从 Vibe 文件中读取原始图片 base64。

        Args:
            file_path: 文件路径（支持 .naiv4vibe/.naiv4vibebundle/.png/.jpg 等）

        Returns:
            图片 base64 字符串
        """
        suffix = file_path.suffix.lower()
        if suffix in (".naiv4vibe", ".naiv4vibebundle", ".json", ".txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("{"):
                data_json = json.loads(content)
                source_data: dict[str, Any] = {}
                if "vibes" in data_json and isinstance(data_json["vibes"], list):
                    if data_json["vibes"]:
                        source_data = data_json["vibes"][0]
                else:
                    source_data = data_json
                return source_data.get("image", "")
            return content  # 纯 base64 文本
        else:
            with open(file_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

    def check_cooldown(self) -> tuple[bool, int]:
        """检查冷却时间。

        Returns:
            (是否就绪, 剩余等待秒数)
        """
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.cooldown:
            return False, int(self.cooldown - elapsed)
        return True, 0

    # ═════════════════════════════════════════════════════════════════════
    #  提示词处理
    # ═════════════════════════════════════════════════════════════════════

    def _merge_negative_prompts(self, user_negative: Optional[str] = None) -> str:
        """合并负面提示词：系统通用 + AI 特殊场景。

        Args:
            user_negative: AI 提供的特殊场景负面提示词

        Returns:
            合并后的完整负面提示词（保留原始顺序）
        """
        base_negative = self.negative_prompt
        if not user_negative:
            return base_negative
        if not base_negative:
            return user_negative
        # 保留 base 顺序，将 user 中不重复的追加到末尾
        base_tags = [tag.strip() for tag in base_negative.split(",") if tag.strip()]
        base_set = {t.lower() for t in base_tags}
        extra = [tag.strip() for tag in user_negative.split(",")
                 if tag.strip() and tag.strip().lower() not in base_set]
        return ", ".join(base_tags + extra)

    # ═════════════════════════════════════════════════════════════════════
    #  Payload 构造（双 API 格式支持）
    # ═════════════════════════════════════════════════════════════════════

    def construct_payload(
        self,
        prompt: str,
        user_id: str,
        is_img2img: bool = False,
        img_base64: Optional[str] = None,
        strength: Optional[float] = None,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        selected_vibe_names: Optional[list[str]] = None,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        reference_images: Optional[list[dict]] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """构造 NovelAI 官方 API 请求 payload。

        Args:
            prompt: 正面提示词
            user_id: 用户 ID
            is_img2img: 是否为图生图
            img_base64: 图生图的原图 base64
            strength: 图生图强度
            negative_prompt: 额外负面提示词
            width: 图片宽度
            height: 图片高度
            scale: 覆盖引导比例（None 则用配置值）
            cfg_rescale: 覆盖提示词引导重新缩放（None 则用配置值）
            reference_images: 精密参考图列表，每项 {"data": base64, "fidelity": float, "strength": float, "type": str}
            character_prompts: 多人物列表，每项 {"prompt": str, "uc": str, "x": float, "y": float}，
                仅 V4 系列模型支持；为 None 或空列表表示单人模式。

        Returns:
            API 请求 payload 字典
        """
        return self._construct_novelai_payload(
            prompt, user_id, is_img2img, img_base64, strength, negative_prompt, width, height,
            selected_vibe_names=selected_vibe_names,
            scale=scale,
            cfg_rescale=cfg_rescale,
            reference_images=reference_images,
            character_prompts=character_prompts,
        )

    def _construct_novelai_payload(
        self,
        prompt: str,
        user_id: str,
        is_img2img: bool,
        img_base64: Optional[str],
        strength: Optional[float],
        negative_prompt: Optional[str],
        width: int,
        height: int,
        selected_vibe_names: Optional[list[str]] = None,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        reference_images: Optional[list[dict]] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """构造 NovelAI 官方 API payload。"""
        is_v4_model = "diffusion-4" in self.model
        is_v3_model = "diffusion-3" in self.model

        # 规范化角色列表，仅 V4 才生效；V3 已在上层入口拦截，这里再防御性忽略
        normalized_chars: list[dict[str, Any]] = []
        if character_prompts and is_v4_model:
            for ch in character_prompts:
                cx = float(ch.get("x", 0.5))
                cy = float(ch.get("y", 0.5))
                normalized_chars.append({
                    "prompt": str(ch.get("prompt", "")).strip(),
                    "uc": str(ch.get("uc", "") or "").strip(),
                    "x": max(0.0, min(1.0, cx)),
                    "y": max(0.0, min(1.0, cy)),
                    "enabled": bool(ch.get("enabled", True)),
                })

        effective_scale = scale if scale is not None else self.scale
        effective_cfg_rescale = cfg_rescale if cfg_rescale is not None else self.prompt_guidance_rescale

        parameters: dict[str, Any] = {
            "width": width,
            "height": height,
            "scale": effective_scale,
            "steps": self.steps,
            "sampler": self.sampler,
            "seed": random.randint(0, 999999999),
            "n_samples": 1,
            "ucPreset": self.uc_preset,
            "qualityToggle": True,
            "sm": False,
            "sm_dyn": False,
            "noise_schedule": self.noise_schedule if is_v4_model else "native",
        }

        if is_v4_model:
            merged_negative = self._merge_negative_prompts(negative_prompt)
            parameters.update({
                "params_version": 3,
                "cfg_rescale": effective_cfg_rescale,
                "autoSmea": False,
                "legacy": False,
                "legacy_v3_extend": False,
                "legacy_uc": False,
                "add_original_image": False,
                "controlnet_strength": 1,
                "dynamic_thresholding": False,
                "prefer_brownian": True,
                "normalize_reference_strength_multiple": False,
                "use_coords": False,
                "deliberate_euler_ancestral_bug": False,
                "skip_cfg_above_sigma": 58 if self.variety_plus else None,
                "characterPrompts": [],
                "v4_prompt": {
                    "caption": {
                        "base_caption": prompt,
                        "char_captions": [],
                    },
                    "use_coords": False,
                    "use_order": True,
                },
                "v4_negative_prompt": {
                    "caption": {
                        "base_caption": merged_negative,
                        "char_captions": [],
                    },
                    "legacy_uc": False,
                },
                "negative_prompt": merged_negative,
                "reference_image_multiple": [],
                "reference_information_extracted_multiple": [],
                "reference_strength_multiple": [],
            })

            # 多人物 / multi-character workspace 字段填充（仅 V4 系列）
            if normalized_chars:
                api_character_prompts: list[dict[str, Any]] = []
                pos_char_captions: list[dict[str, Any]] = []
                neg_char_captions: list[dict[str, Any]] = []
                for ch in normalized_chars:
                    center = {"x": ch["x"], "y": ch["y"]}
                    api_character_prompts.append({
                        "prompt": ch["prompt"],
                        "uc": ch["uc"],
                        "center": center,
                        "enabled": ch["enabled"],
                    })
                    pos_char_captions.append({
                        "char_caption": ch["prompt"],
                        "centers": [center],
                    })
                    neg_char_captions.append({
                        "char_caption": ch["uc"],
                        "centers": [center],
                    })
                parameters["characterPrompts"] = api_character_prompts
                parameters["v4_prompt"]["caption"]["char_captions"] = pos_char_captions
                parameters["v4_negative_prompt"]["caption"]["char_captions"] = neg_char_captions
                # 启用坐标定位：always_use_coords 控制全局开关
                parameters["use_coords"] = bool(self.always_use_coords)
                parameters["v4_prompt"]["use_coords"] = bool(self.always_use_coords)
                logger.info(
                    f"多人物模式启用：共 {len(normalized_chars)} 个角色，"
                    f"use_coords={self.always_use_coords}"
                )

            # 精密参考（Director Tools）—— 使用独立 API 字段，与 Vibe Transfer 互不干扰
            if reference_images:
                parameters["director_reference_images"] = [r["data"] for r in reference_images]
                parameters["director_reference_descriptions"] = [
                    {
                        "caption": {
                            "base_caption": r.get("type", "character&style"),
                            "char_captions": [],
                        },
                        "legacy_uc": False,
                    }
                    for r in reference_images
                ]
                parameters["director_reference_strength_values"] = [
                    round(r.get("strength", 1.0), 2) for r in reference_images
                ]
                parameters["director_reference_secondary_strength_values"] = [
                    round(1.0 - r.get("fidelity", 1.0), 2) for r in reference_images
                ]
                parameters["director_reference_information_extracted"] = [
                    1.0 for _ in reference_images
                ]
                logger.info(f"注入精密参考图：共 {len(reference_images)} 张")

            # Vibe 参考图注入（预设 + 用户手动添加）
            # 注意：精密参考（Director Reference）与 Vibe Transfer 互斥，有精密参考时跳过 Vibe
            if not is_img2img and not reference_images:
                vibes_to_inject: list[dict[str, Any]] = []

                # 始终注入的 Vibe（always_inject 时）
                if self.always_inject and self.preset_vibes:
                    vibes_to_inject.extend(self.preset_vibes)
                    logger.info(f"注入预设 Vibe：共 {len(self.preset_vibes)} 个")

                # LLM 选择的可选 Vibe
                if self.selectable_enabled and selected_vibe_names and self.selectable_vibes:
                    for name in selected_vibe_names:
                        vibe = self.selectable_vibes.get(name)
                        if vibe:
                            vibes_to_inject.append(vibe)
                        else:
                            logger.warning(f"LLM 选择了不存在的 Vibe: {name!r}，跳过")
                    if selected_vibe_names:
                        logger.info(f"LLM 选择的 Vibe: {selected_vibe_names}")

                # 用户手动添加的 Vibe
                user_vibes = self.user_vibes.get(user_id, [])
                if user_vibes:
                    vibes_to_inject.extend(user_vibes)
                    logger.info(f"User {user_id} 手动 Vibe 注入：共 {len(user_vibes)} 个")

                if vibes_to_inject:
                    parameters["reference_image_multiple"] = [v["data"] for v in vibes_to_inject]
                    parameters["reference_information_extracted_multiple"] = [v["ie"] for v in vibes_to_inject]
                    parameters["reference_strength_multiple"] = [v["strength"] for v in vibes_to_inject]

        elif is_v3_model:
            parameters["negative_prompt"] = self._merge_negative_prompts(negative_prompt)

        payload: dict[str, Any] = {
            "input": prompt,
            "model": self.model,
            "action": "generate",
            "parameters": parameters,
        }

        # 图生图
        if is_img2img and img_base64:
            if strength is None:
                strength = self.img2img_default_strength
            img2img_seed = random.randint(0, 999999999)
            payload["action"] = "img2img"
            payload["parameters"].update({
                "image": img_base64,
                "strength": strength,
                "noise": 0.0,
                "extra_noise_seed": img2img_seed,
                "img2img": {"color_correct": True, "strength": strength},
                # 返回完整图像，避免只得到未与原图合成的重绘结果。
                "add_original_image": True,
                "inpaintImg2ImgStrength": strength,
            })

        return payload

    # ═════════════════════════════════════════════════════════════════════
    #  图片生成
    # ═════════════════════════════════════════════════════════════════════

    async def generate_image(
        self,
        prompt: str,
        user_id: str,
        group_id: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        is_img2img: bool = False,
        img_base64: Optional[str] = None,
        strength: Optional[float] = None,
        from_command: bool = False,
        selected_vibe_names: Optional[list[str]] = None,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        reference_images: Optional[list[dict]] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """生成图片（通过队列串行执行，防止 429 封号）。

        Args:
            prompt: 正面提示词
            user_id: 用户 ID
            group_id: 群组 ID（可选）
            negative_prompt: 额外负面提示词
            width: 图片宽度
            height: 图片高度
            is_img2img: 是否为图生图
            img_base64: 图生图的原图 base64
            strength: 图生图强度
            from_command: 是否来自命令调用
            scale: 覆盖引导比例（None 则用配置值）
            cfg_rescale: 覆盖提示词引导重新缩放（None 则用配置值）
            reference_images: 精密参考图列表，每项 {"data": base64, "fidelity": float, "strength": float, "type": str}

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        # V3 模型不支持多人物 / multi-character workspace，直接前置拦截避免空跑
        if character_prompts:
            if "diffusion-4" not in self.model:
                return (
                    False,
                    f"当前模型 {self.model!r} 不支持多人物生图（仅 V4 系列支持），"
                    "请先把 generation.model 切到 nai-diffusion-4-* 后再试",
                    None,
                )
            if len(character_prompts) > self.max_characters:
                return (
                    False,
                    f"角色数量 {len(character_prompts)} 超过上限 {self.max_characters}，"
                    "请合并角色或调高 advanced.max_characters 配置",
                    None,
                )

        async def task() -> tuple[bool, str, Optional[str]]:
            return await self._generate_image_internal(
                prompt, user_id, group_id, negative_prompt,
                width, height, is_img2img, img_base64, strength,
                from_command=from_command,
                selected_vibe_names=selected_vibe_names,
                scale=scale,
                cfg_rescale=cfg_rescale,
                reference_images=reference_images,
                character_prompts=character_prompts,
            )

        return await self._enqueue_task(task)

    async def _generate_image_internal(
        self,
        prompt: str,
        user_id: str,
        group_id: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        is_img2img: bool = False,
        img_base64: Optional[str] = None,
        strength: Optional[float] = None,
        from_command: bool = False,
        selected_vibe_names: Optional[list[str]] = None,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        reference_images: Optional[list[dict]] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """内部生成图片方法（实际执行逻辑）。根据 channel 配置分发到不同渠道。"""
        # 检查冷却
        is_ready, wait_time = self.check_cooldown()
        if not is_ready:
            logger.info(f"需要等待冷却 {wait_time} 秒，队列中等待...")
            await asyncio.sleep(wait_time)

        # 检查 API Key
        api_key = self._get_current_api_key()
        if not api_key:
            return False, "API Key 没配置，联系管理员看看", None

        # 更新最后请求时间
        self.last_request_time = time.time()

        # 根据渠道分发
        if self.channel == "gateway":
            try:
                return await self._generate_via_gateway(
                    prompt=prompt,
                    api_key=api_key,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    scale=scale,
                    cfg_rescale=cfg_rescale,
                    character_prompts=character_prompts,
                    reference_images=reference_images,
                    from_command=from_command,
                    is_img2img=is_img2img,
                    img_base64=img_base64,
                    strength=strength,
                    selected_vibe_names=selected_vibe_names,
                )
            except Exception as e:
                logger.error(f"Gateway 渠道生图失败: {e}")
                return False, f"生成失败了：{e!s}", None

        # 官方渠道（默认）
        # 图生图自动缩放：原图超过 1M 像素时等比缩放到 Opus 免费范围
        if is_img2img and img_base64 and self.img2img_auto_downscale:
            from ..utils.image_utils import ImageUtils
            orig_w, orig_h = ImageUtils.get_image_size_from_b64(img_base64)
            if orig_w and orig_h and orig_w * orig_h > 1048576:
                img_base64, width, height = ImageUtils.downscale_image_b64(
                    img_base64, max_pixels=1048576, align=64
                )
                logger.info(f"图生图原图自动缩放: {orig_w}x{orig_h} → {width}x{height}")

        # 构造 payload
        payload = self.construct_payload(
            prompt, user_id, is_img2img, img_base64, strength,
            negative_prompt=negative_prompt, width=width, height=height,
            selected_vibe_names=selected_vibe_names,
            scale=scale,
            cfg_rescale=cfg_rescale,
            reference_images=reference_images,
            character_prompts=character_prompts,
        )

        # 提交请求并获取结果
        try:
            result = await self._submit_and_poll(
                payload, api_key, from_command=from_command,
            )
            return result
        except Exception as e:
            logger.error(f"图片生成失败: {e}")
            return False, f"生成失败了：{e!s}", None

    # ═════════════════════════════════════════════════════════════════════
    #  API 提交与轮询
    # ═════════════════════════════════════════════════════════════════════

    async def _submit_and_poll(
        self,
        payload: dict[str, Any],
        api_key: str,
        retry_on_404: bool = False,
        from_command: bool = False,
    ) -> tuple[Any, str, Any]:
        """提交请求至 NovelAI 官方 API 并获取结果。

        Args:
            payload: 请求参数
            api_key: API 密钥
            retry_on_404: 遇到连续 404 时是否自动重试
            from_command: 是否来自命令调用
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/zip",
            "Origin": "https://novelai.net",
            "Referer": "https://novelai.net",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
        }

        if self.proxy:
            logger.info("图片请求使用已配置代理")

        # 网络级重试（超时/连接错误，最多 2 次重试）
        max_net_retries = 2
        net_retry_delay = 5

        for net_attempt in range(max_net_retries + 1):
            try:
                session = await self._get_http_session()
                request_kwargs: dict[str, Any] = {
                    "json": payload,
                    "headers": headers,
                    "timeout": aiohttp.ClientTimeout(total=60),
                }
                if self.proxy:
                    request_kwargs["proxy"] = self.proxy

                max_429_retries = 3
                retry_delay = 20

                for attempt in range(max_429_retries + 1):
                    async with session.post(self.base_url, **request_kwargs) as resp:
                        if resp.status == 429:
                            if attempt < max_429_retries:
                                logger.warning(
                                    f"遇到 429 错误，{retry_delay} 秒后重试 "
                                    f"(尝试 {attempt + 1}/{max_429_retries})"
                                )
                                await asyncio.sleep(retry_delay)
                                continue
                            self._rotate_api_key()
                            return (
                                False,
                                f"请求太频繁了，已经重试了 {attempt} 次，等会儿再试吧",
                                None,
                            )

                        if resp.status not in (200, 201):
                            err = await resp.text()
                            try:
                                parsed = json.loads(err)
                                msg = parsed.get("message", err)
                            except json.JSONDecodeError:
                                msg = err
                            return False, f"请求失败了 ({resp.status}): {str(msg)[:500]}", None

                        img_data = await resp.read()
                        if img_data[:4] == b"PK\x03\x04":
                            return await self._extract_image_from_zip(
                                img_data,
                                from_command=from_command,
                            )
                        return await self._save_image_from_bytes(
                            img_data,
                            from_command=from_command,
                        )

            except asyncio.TimeoutError:
                if net_attempt < max_net_retries:
                    logger.warning(
                        f"请求超时，{net_retry_delay}s 后重试 "
                        f"({net_attempt + 1}/{max_net_retries})"
                    )
                    await asyncio.sleep(net_retry_delay)
                    continue
                return False, "请求超时了，网络不太好", None
            except Exception as e:
                if net_attempt < max_net_retries:
                    logger.warning(
                        f"网络错误，{net_retry_delay}s 后重试 "
                        f"({net_attempt + 1}/{max_net_retries}): {e}"
                    )
                    await asyncio.sleep(net_retry_delay)
                    continue
                logger.error(f"请求异常: {e}", exc_info=True)
                return False, f"网络出问题了：{e}", None

        return False, "未知错误", None

    # ═════════════════════════════════════════════════════════════════════
    #  图片保存辅助方法
    # ═════════════════════════════════════════════════════════════════════

    async def _extract_image_from_zip(
        self, zip_data: bytes, *, from_command: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        """从 ZIP 文件中提取图片。"""
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for entry in zf.infolist():
                    if entry.is_dir():
                        continue
                    if entry.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        img_data = zf.read(entry)
                        return await self._save_image_from_bytes(
                            img_data,
                            from_command=from_command,
                        )
            return False, "ZIP 文件中没有找到图片", None
        except Exception as e:
            logger.error(f"解压 ZIP 失败: {e}")
            return False, f"解压失败: {e}", None

    async def _save_image_from_bytes(
        self, img_data: bytes, *, from_command: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        """从字节数据保存图片。

        Args:
            img_data: 图片字节数据
            from_command: 是否来自命令调用（True = command_images, False = temp_images）
        """
        try:
            if not img_data:
                return False, "图片数据为空", None
            try:
                import io

                with Image.open(io.BytesIO(img_data)) as image:
                    width, height = image.size
                    image_format = image.format
            except Exception:
                width, height = 0, 0
                image_format = "unknown"

            save_dir = self.command_images_dir if from_command else self.temp_dir
            filename = f"{uuid.uuid4()}.png"
            filepath = save_dir / filename
            save_dir.mkdir(parents=True, exist_ok=True)

            with open(filepath, "wb") as f:
                f.write(img_data)
                f.flush()
                os.fsync(f.fileno())

            actual_size = filepath.stat().st_size
            if actual_size != len(img_data):
                logger.warning(f"文件大小不匹配: 期望 {len(img_data)}, 实际 {actual_size}")

            logger.info(
                f"图片已保存: {filepath}, 格式: {image_format}, 尺寸: {width}x{height}"
            )
            return True, "图片生成成功", str(filepath)
        except Exception as e:
            logger.error(f"保存图片失败: {e}", exc_info=True)
            return False, f"保存图片失败: {e}", None

    # ═════════════════════════════════════════════════════════════════════
    #  Vibe 管理
    # ═════════════════════════════════════════════════════════════════════

    def add_vibe(self, user_id: str, vibe_data: dict[str, Any]) -> tuple[bool, str]:
        """添加 Vibe 到用户缓存。"""
        if user_id not in self.user_vibes:
            self.user_vibes[user_id] = []

        if len(self.user_vibes[user_id]) >= self.max_vibes:
            return False, f"最多同时叠加 {self.max_vibes} 个 Vibe"

        self.user_vibes[user_id].append(vibe_data)
        count = len(self.user_vibes[user_id])
        return True, f"已添加 Vibe ({count}/{self.max_vibes})"

    def clear_vibes(self, user_id: str) -> str:
        """清空用户的 Vibe 缓存。"""
        self.user_vibes[user_id] = []
        return "已清空所有 Vibe 设置"

    def get_vibe_status(self, user_id: str) -> str:
        """获取用户的 Vibe 状态。"""
        vibes = self.user_vibes.get(user_id, [])
        if not vibes:
            return "当前未加载任何 Vibe"

        msg = f"当前已加载 {len(vibes)} 个 Vibe:\n"
        for i, v in enumerate(vibes):
            msg += f"{i + 1}. IE:{v['ie']}, Str:{v['strength']}\n"
        return msg

    def list_vibe_files(self) -> tuple[bool, str]:
        """列出 Vibe 素材库文件。"""
        try:
            allowed_extensions = (
                ".png", ".jpg", ".jpeg", ".webp",
                ".txt", ".json", ".naiv4vibe", ".naiv4vibebundle",
            )
            files = [
                f.name for f in self.vibe_storage_dir.iterdir()
                if f.is_file() and f.suffix.lower() in allowed_extensions
            ]

            if not files:
                return True, "素材库为空"

            msg = "素材库文件列表:\n" + "\n".join([f"• {f}" for f in files])
            msg += "\n\n使用 /nai_vibe add [文件名] 加载素材"
            return True, msg

        except Exception as e:
            logger.error(f"列出素材库文件失败: {e}")
            return False, f"读取素材库失败: {e}"

    async def load_vibe_from_file(self, user_id: str, file_name: str) -> tuple[bool, str]:
        """从文件加载 Vibe。

        Args:
            user_id: 用户 ID
            file_name: 文件名（支持模糊匹配）

        Returns:
            (是否成功, 消息)
        """
        requested_name = file_name.strip()
        normalized_name = requested_name.replace("\\", "/")
        if (
            not requested_name
            or Path(requested_name).is_absolute()
            or "/" in normalized_name
            or Path(normalized_name).name != normalized_name
        ):
            return False, "Vibe 文件名不合法"

        final_filename: str | None = None
        exact_path = self.vibe_storage_dir / requested_name

        if exact_path.is_file():
            final_filename = requested_name
        else:
            try:
                allowed_extensions = (
                    ".png", ".jpg", ".jpeg", ".webp",
                    ".txt", ".json", ".naiv4vibe", ".naiv4vibebundle",
                )
                all_files = [
                    f.name for f in self.vibe_storage_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in allowed_extensions
                ]
                candidates = [f for f in all_files if requested_name.lower() in f.lower()]

                if len(candidates) == 0:
                    return False, f"素材库中找不到包含 '{requested_name}' 的文件"
                elif len(candidates) == 1:
                    final_filename = candidates[0]
                else:
                    msg = f"找到 {len(candidates)} 个类似文件，请提供更精确的名称:\n"
                    msg += "\n".join([f"• {c}" for c in candidates[:5]])
                    if len(candidates) > 5:
                        msg += f"\n...等共 {len(candidates)} 个"
                    return False, msg
            except Exception as e:
                return False, f"读取素材库失败: {e}"

        if final_filename is None:
            return False, "未找到 Vibe 文件"
        file_path = (self.vibe_storage_dir / final_filename).resolve()
        storage_root = self.vibe_storage_dir.resolve()
        if storage_root not in file_path.parents:
            return False, "Vibe 文件路径越界"

        try:
            target_ie = 1.0
            target_strength = 0.6

            raw_image_b64 = self._read_image_b64_from_vibe_file(file_path)

            if not raw_image_b64:
                return False, "文件数据无效或未找到 image 字段"

            # 调用 /ai/encode-vibe 端点编码，API 要求传入编码后的向量，而非原始图片
            encoded = await self._encode_vibe(raw_image_b64, target_ie)
            if not encoded:
                return False, "Vibe 编码失败，请检查 API Key 和网络连接"

            vibe_obj = {
                "data": encoded,
                "ie": target_ie,
                "strength": target_strength,
            }

            success, msg = self.add_vibe(user_id, vibe_obj)
            if not success:
                return False, msg

            current_index = len(self.user_vibes[user_id])
            result_msg = f"已添加【{final_filename}】\n"
            result_msg += f"{current_index}. IE:{target_ie}, Str:{target_strength}"

            return True, result_msg

        except Exception as e:
            logger.error(f"加载 Vibe 文件失败: {e}")
            return False, f"加载失败: {e}"

    # ═════════════════════════════════════════════════════════════════════
    #  Gateway 渠道（OpenAI Chat Completions 兼容接口）
    # ═════════════════════════════════════════════════════════════════════

    async def _generate_via_gateway(
        self,
        prompt: str,
        api_key: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
        reference_images: Optional[list[dict]] = None,
        from_command: bool = False,
        is_img2img: bool = False,
        img_base64: Optional[str] = None,
        strength: Optional[float] = None,
        selected_vibe_names: Optional[list[str]] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """通过 NovelAI Gateway 图片扩展接口生成或编辑图片。

        根据请求内容选择文生图、图生图或 Vibe Transfer 端点。

        Args:
            prompt: 正面提示词
            api_key: NovelAI API Key（pst-*）
            negative_prompt: 额外负面提示词（会与全局负面词合并）
            width: 图片宽度（只接受 832 / 1024 / 1216）
            height: 图片高度（只接受 832 / 1024 / 1216）
            scale: 提示词引导强度（覆盖配置值）
            cfg_rescale: CFG 缩放比例（覆盖配置值）
            character_prompts: 多人物列表，每项 {"prompt", "uc", "x", "y"}
            reference_images: V4.5 人物或风格精确参考列表
            from_command: 是否来自命令调用
            is_img2img: 是否为图生图
            img_base64: 图生图的原图 base64
            strength: 图生图强度
            selected_vibe_names: LLM 选择的 Vibe 名称列表

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        # 图生图：使用 /v1/images/img2img 端点
        if is_img2img and img_base64:
            return await self._gateway_img2img(
                prompt=prompt,
                api_key=api_key,
                img_base64=img_base64,
                strength=strength,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                scale=scale,
                cfg_rescale=cfg_rescale,
                from_command=from_command,
            )

        # Vibe Transfer：使用 /v1/images/vibe-transfer 端点
        vibes_to_transfer = self._collect_vibes_for_gateway(selected_vibe_names)
        if vibes_to_transfer:
            return await self._gateway_vibe_transfer(
                prompt=prompt,
                api_key=api_key,
                vibes=vibes_to_transfer,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                scale=scale,
                cfg_rescale=cfg_rescale,
                from_command=from_command,
            )

        url = f"{self.gateway_base_url}/v1/images/generations"
        merged_negative = self._merge_negative_prompts(negative_prompt)
        effective_scale = scale if scale is not None else self.scale
        effective_cfg_rescale = (
            cfg_rescale
            if cfg_rescale is not None
            else self.prompt_guidance_rescale
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "negative_prompt": merged_negative,
            "size": f"{width}x{height}",
            "n": 1,
            "steps": self.steps,
            "scale": effective_scale,
            "cfg_rescale": effective_cfg_rescale,
            "sampler": self.sampler,
            "noise_schedule": self.noise_schedule,
            "ucPreset": self.uc_preset,
            "quality": True,
            "variety_boost": self.variety_plus,
            "use_coords": bool(character_prompts and self.always_use_coords),
            "response_format": "b64_json",
        }
        if character_prompts:
            payload["characters"] = [
                {
                    "prompt": str(character.get("prompt", "")),
                    "negative_prompt": str(character.get("uc", "") or ""),
                    "position": [
                        float(character.get("x", 0.5)),
                        float(character.get("y", 0.5)),
                    ],
                    "enabled": bool(character.get("enabled", True)),
                }
                for character in character_prompts
            ]
        if reference_images:
            payload["character_references"] = [
                {
                    "image": reference["data"],
                    "type": reference.get("type", "character&style"),
                    "strength": reference.get("strength", 1.0),
                    "fidelity": reference.get("fidelity", 1.0),
                    "information_extracted": 1.0,
                }
                for reference in reference_images
            ]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            f"[Gateway generations] POST {url} | model={self.model} | "
            f"{width}x{height} | characters={len(character_prompts or [])} | "
            f"references={len(reference_images or [])}"
        )

        max_net_retries = 2
        net_retry_delay = 5

        for net_attempt in range(max_net_retries + 1):
            try:
                session = await self._get_http_session()
                request_kwargs = self._request_kwargs(
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                )
                async with session.post(url, **request_kwargs) as resp:
                        if resp.status == 429:
                            if net_attempt < max_net_retries:
                                logger.warning(
                                    f"[Gateway] 遇到 429，{net_retry_delay}s 后重试 "
                                    f"({net_attempt + 1}/{max_net_retries})"
                                )
                                await asyncio.sleep(net_retry_delay)
                                continue
                            self._rotate_api_key()
                            return False, "请求太频繁了，等会儿再试吧", None

                        if resp.status not in (200, 201):
                            err = await resp.text()
                            try:
                                import orjson
                                msg = orjson.loads(err).get("message", err)
                            except Exception:
                                msg = err
                            return False, f"Gateway 请求失败 ({resp.status}): {msg}", None

                        data = await resp.json()
                        return await self._save_gateway_response(
                            data,
                            api_key,
                            from_command=from_command,
                        )

            except asyncio.TimeoutError:
                if net_attempt < max_net_retries:
                    logger.warning(
                        f"[Gateway] 请求超时，{net_retry_delay}s 后重试 "
                        f"({net_attempt + 1}/{max_net_retries})"
                    )
                    await asyncio.sleep(net_retry_delay)
                    continue
                return False, "Gateway 请求超时了，网络不太好", None
            except Exception as e:
                if net_attempt < max_net_retries:
                    logger.warning(
                        f"[Gateway] 网络错误，{net_retry_delay}s 后重试 "
                        f"({net_attempt + 1}/{max_net_retries}): {e}"
                    )
                    await asyncio.sleep(net_retry_delay)
                    continue
                logger.error(f"[Gateway] 请求异常: {e}", exc_info=True)
                return False, f"Gateway 网络出问题了：{e}", None

        return False, "未知错误", None

    async def _download_and_save_gateway_image(
        self,
        image_url: str,
        api_key: str,
        *,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """从 Gateway 返回的 URL 下载图片并保存到本地。

        Args:
            image_url: Gateway 返回的图片 URL
            api_key: 用于认证的 API Key（部分 gateway 实现需要）
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        try:
            session = await self._get_http_session()
            request_kwargs = self._request_kwargs(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            )
            async with session.get(image_url, **request_kwargs) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    return False, f"下载图片失败 ({resp.status}): {err[:200]}", None
                img_data = await resp.read()

            return await self._save_image_from_bytes(img_data, from_command=from_command)

        except Exception as e:
            logger.error(f"[Gateway] 下载图片失败: {e}", exc_info=True)
            return False, f"下载图片失败：{e}", None

    async def get_user_info(self) -> tuple[bool, str]:
        """NovelAI 官方 API 不提供账号信息查询。"""
        return False, "NovelAI 官方 API 不支持账号信息查询"

    # ═════════════════════════════════════════════════════════════════════
    #  Gateway 图片生成与参考图能力
    # ═════════════════════════════════════════════════════════════════════

    def _collect_vibes_for_gateway(
        self, selected_vibe_names: Optional[list[str]]
    ) -> list[dict[str, Any]]:
        """收集要注入的 Vibe 数据列表（供 gateway vibe-transfer 端点使用）。

        Args:
            selected_vibe_names: LLM 选择的 Vibe 名称列表

        Returns:
            Vibe 数据列表，每项 {"data", "ie", "strength"}
        """
        vibes: list[dict[str, Any]] = []
        if self.always_inject and self.preset_vibes:
            vibes.extend(self.preset_vibes)
        if self.selectable_enabled and selected_vibe_names and self.selectable_vibes:
            for name in selected_vibe_names:
                vibe = self.selectable_vibes.get(name)
                if vibe:
                    vibes.append(vibe)
                else:
                    logger.warning(f"[Gateway] LLM 选择了不存在的 Vibe: {name!r}，跳过")
        return vibes

    async def _gateway_img2img(
        self,
        prompt: str,
        api_key: str,
        img_base64: str,
        strength: Optional[float] = None,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """Gateway 渠道图生图：POST /v1/images/img2img。

        网关自带自动缩放机制，超过免费像素上限会等比缩放，不消耗 Anlas。

        Args:
            prompt: 正面提示词
            api_key: API Key
            img_base64: 原图 base64
            strength: 重绘强度（0.01-1.0）
            negative_prompt: 额外负面提示词
            width: 目标宽度
            height: 目标高度
            scale: 提示词引导强度
            cfg_rescale: CFG 缩放比例
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        url = f"{self.gateway_base_url}/v1/images/img2img"
        effective_strength = strength if strength is not None else self.img2img_default_strength
        effective_scale = scale if scale is not None else self.scale
        effective_cfg_rescale = cfg_rescale if cfg_rescale is not None else self.prompt_guidance_rescale
        merged_negative = self._merge_negative_prompts(negative_prompt)

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "image": img_base64,
            "strength": effective_strength,
            "size": f"{width}x{height}",
            "scale": effective_scale,
            "cfg_rescale": effective_cfg_rescale,
            "sampler": self.sampler,
            "noise_schedule": self.noise_schedule,
            "negative_prompt": merged_negative,
            "response_format": "b64_json",
        }

        logger.info(f"[Gateway img2img] POST {url} | {width}x{height} | strength={effective_strength}")
        return await self._gateway_post_and_save(url, payload, api_key, from_command=from_command)

    async def _gateway_vibe_transfer(
        self,
        prompt: str,
        api_key: str,
        vibes: list[dict[str, Any]],
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """Gateway 渠道 Vibe Transfer：POST /v1/images/vibe-transfer。

        使用已编码的 vibe 数据（模式2），跳过编码，不消耗编码费。

        Args:
            prompt: 正面提示词
            api_key: API Key
            vibes: Vibe 数据列表，每项 {"data", "ie", "strength"}
            negative_prompt: 额外负面提示词
            width: 目标宽度
            height: 目标高度
            scale: 提示词引导强度
            cfg_rescale: CFG 缩放比例
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        url = f"{self.gateway_base_url}/v1/images/vibe-transfer"
        effective_scale = scale if scale is not None else self.scale
        effective_cfg_rescale = cfg_rescale if cfg_rescale is not None else self.prompt_guidance_rescale

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "reference_image_multiple": [v["data"] for v in vibes],
            "reference_strength_multiple": [v["strength"] for v in vibes],
            "reference_information_extracted_multiple": [v["ie"] for v in vibes],
            "width": width,
            "height": height,
            "scale": effective_scale,
            "cfg_rescale": effective_cfg_rescale,
            "response_format": "b64_json",
        }

        logger.info(f"[Gateway vibe-transfer] POST {url} | {len(vibes)} vibes | {width}x{height}")
        return await self._gateway_post_and_save(url, payload, api_key, from_command=from_command)

    async def _gateway_post_and_save(
        self,
        url: str,
        payload: dict[str, Any],
        api_key: str,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """Gateway 端点通用 POST 请求并保存返回的图片。

        支持两种响应格式：
        - b64_json：直接从 data[0].b64_json 解码保存
        - url（fallback）：从 data[0].url 或 Markdown 链接中提取 URL 下载

        Args:
            url: 请求 URL
            payload: 请求体
            api_key: API Key
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        max_net_retries = 2
        net_retry_delay = 5

        for net_attempt in range(max_net_retries + 1):
            try:
                session = await self._get_http_session()
                request_kwargs = self._request_kwargs(
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                )
                async with session.post(url, **request_kwargs) as resp:
                        if resp.status == 429:
                            if net_attempt < max_net_retries:
                                logger.warning(
                                    f"[Gateway] 遇到 429，{net_retry_delay}s 后重试 "
                                    f"({net_attempt + 1}/{max_net_retries})"
                                )
                                await asyncio.sleep(net_retry_delay)
                                continue
                            self._rotate_api_key()
                            return False, "请求太频繁了，等会儿再试吧", None

                        if resp.status not in (200, 201):
                            err = await resp.text()
                            try:
                                import orjson
                                err_data = orjson.loads(err)
                                msg = err_data.get("error", {}).get("message", err)
                            except Exception:
                                msg = err
                            return False, f"Gateway 请求失败 ({resp.status}): {msg}", None

                        data = await resp.json()
                        return await self._save_gateway_response(
                            data,
                            api_key,
                            from_command=from_command,
                        )

            except asyncio.TimeoutError:
                if net_attempt < max_net_retries:
                    logger.warning(f"[Gateway] 请求超时，{net_retry_delay}s 后重试 ({net_attempt + 1}/{max_net_retries})")
                    await asyncio.sleep(net_retry_delay)
                    continue
                return False, "Gateway 请求超时了，网络不太好", None
            except Exception as e:
                if net_attempt < max_net_retries:
                    logger.warning(f"[Gateway] 网络错误，{net_retry_delay}s 后重试 ({net_attempt + 1}/{max_net_retries}): {e}")
                    await asyncio.sleep(net_retry_delay)
                    continue
                logger.error(f"[Gateway] 请求异常: {e}", exc_info=True)
                return False, f"Gateway 网络出问题了：{e}", None

        return False, "未知错误", None

    async def _save_gateway_response(
        self,
        data: dict[str, Any],
        api_key: str,
        *,
        from_command: bool,
    ) -> tuple[bool, str, Optional[str]]:
        """保存 Gateway OpenAI 图片响应中的首张图片。"""

        try:
            image = data["data"][0]
        except (KeyError, IndexError, TypeError):
            return False, "Gateway 响应中未找到图片数据", None
        b64_image = image.get("b64_json")
        if isinstance(b64_image, str) and b64_image:
            return await self._save_image_from_b64(
                b64_image,
                from_command=from_command,
            )
        image_url = image.get("url")
        if isinstance(image_url, str) and image_url:
            return await self._download_and_save_gateway_image(
                image_url,
                api_key,
                from_command=from_command,
            )
        return False, "Gateway 响应中未找到图片数据", None

    async def _save_image_from_b64(
        self, b64_data: str, from_command: bool = False
    ) -> tuple[bool, str, Optional[str]]:
        """从 base64 字符串保存图片到本地。

        Args:
            b64_data: base64 编码的图片数据（支持带 data:image/...;base64, 前缀）
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        from ..utils.image_utils import ImageUtils
        save_dir = self.command_images_dir if from_command else self.temp_dir
        filepath = ImageUtils.save_b64_to_file(b64_data, save_dir, prefix="gateway")
        if not filepath:
            return False, "保存图片失败", None
        logger.info(f"[Gateway] 图片已保存: {filepath}")
        return True, "图片生成成功", filepath

    # ═════════════════════════════════════════════════════════════════════
    #  局部重绘、图像放大、标签建议与导演工具
    # ═════════════════════════════════════════════════════════════════════

    async def inpaint_image(
        self,
        prompt: str,
        image_b64: str,
        mask_b64: str,
        user_id: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        strength: float = 1.0,
        scale: Optional[float] = None,
        cfg_rescale: Optional[float] = None,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """局部重绘——保留遮罩外的区域，仅重绘遮罩覆盖部分。

        双渠道分发：official 使用 action=infill，gateway 使用 /v1/images/inpainting。

        Args:
            prompt: 正面提示词
            image_b64: 源图 base64
            mask_b64: 遮罩 base64（RGBA PNG，白色=重绘）
            user_id: 用户 ID
            negative_prompt: 额外负面提示词
            width: 目标宽度
            height: 目标高度
            strength: 重绘强度（0.01-1.0）
            scale: 覆盖引导比例
            cfg_rescale: 覆盖 cfg_rescale
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        async def task() -> tuple[bool, str, Optional[str]]:
            return await self._inpaint_image_internal(
                prompt, image_b64, mask_b64, user_id,
                negative_prompt, width, height, strength,
                scale, cfg_rescale, from_command,
            )
        return await self._enqueue_task(task)

    async def _inpaint_image_internal(
        self,
        prompt: str,
        image_b64: str,
        mask_b64: str,
        user_id: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        strength: float,
        scale: Optional[float],
        cfg_rescale: Optional[float],
        from_command: bool,
    ) -> tuple[bool, str, Optional[str]]:
        """局部重绘内部实现。"""
        is_ready, wait_time = self.check_cooldown()
        if not is_ready:
            await asyncio.sleep(wait_time)

        api_key = self._get_current_api_key()
        if not api_key:
            return False, "API Key 没配置", None

        self.last_request_time = time.time()

        if self.channel == "gateway":
            url = f"{self.gateway_base_url}/v1/images/inpainting"
            merged_negative = self._merge_negative_prompts(negative_prompt)
            effective_scale = scale if scale is not None else self.scale
            effective_cfg_rescale = cfg_rescale if cfg_rescale is not None else self.prompt_guidance_rescale
            payload = {
                "model": self.model,
                "prompt": prompt,
                "image": image_b64,
                "mask": mask_b64,
                "strength": strength,
                "size": f"{width}x{height}",
                "scale": effective_scale,
                "cfg_rescale": effective_cfg_rescale,
                "sampler": self.sampler,
                "noise_schedule": self.noise_schedule,
                "negative_prompt": merged_negative,
                "response_format": "b64_json",
            }
            logger.info(f"[Gateway inpainting] {width}x{height} | strength={strength}")
            return await self._gateway_post_and_save(url, payload, api_key, from_command=from_command)

        # official 渠道：action=infill
        infill_model = f"{self.model}-inpainting" if not self.model.endswith("-inpainting") else self.model
        payload = self._construct_infill_payload(
            prompt, image_b64, mask_b64, infill_model,
            negative_prompt, width, height, strength, scale, cfg_rescale,
        )
        logger.info(f"[Official infill] {width}x{height} | strength={strength}")
        return await self._submit_and_poll(payload, api_key, from_command=from_command)

    def _construct_infill_payload(
        self,
        prompt: str,
        image_b64: str,
        mask_b64: str,
        model: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        strength: float,
        scale: Optional[float],
        cfg_rescale: Optional[float],
    ) -> dict[str, Any]:
        """构造 official 渠道局部重绘 payload。"""
        is_v4_model = "diffusion-4" in self.model
        effective_scale = scale if scale is not None else self.scale
        effective_cfg_rescale = cfg_rescale if cfg_rescale is not None else self.prompt_guidance_rescale
        merged_negative = self._merge_negative_prompts(negative_prompt)
        seed = random.randint(0, 999999999)

        parameters: dict[str, Any] = {
            "width": width,
            "height": height,
            "scale": effective_scale,
            "steps": self.steps,
            "sampler": self.sampler,
            "seed": seed,
            "n_samples": 1,
            "ucPreset": self.uc_preset,
            "qualityToggle": True,
            "sm": False,
            "sm_dyn": False,
            "noise_schedule": self.noise_schedule if is_v4_model else "native",
            "image": image_b64,
            "mask": mask_b64,
            "strength": strength,
            "noise": 0,
            "extra_noise_seed": seed,
            "img2img": {"color_correct": True, "strength": 1.0},
            "inpaintImg2ImgStrength": strength,
            # infill 必须叠加原图：模型输出只包含重绘区域，保留区域无像素。
            # True 让 API 把原图叠回保留区域，得到完整图片；False 时保留区全黑/透明。
            "add_original_image": True,
        }

        if is_v4_model:
            parameters.update({
                "params_version": 3,
                "cfg_rescale": effective_cfg_rescale,
                "autoSmea": False,
                "legacy": False,
                "legacy_v3_extend": False,
                "legacy_uc": False,
                "controlnet_strength": 1,
                "dynamic_thresholding": False,
                "prefer_brownian": True,
                "normalize_reference_strength_multiple": False,
                "use_coords": False,
                "deliberate_euler_ancestral_bug": False,
                "skip_cfg_above_sigma": 58 if self.variety_plus else None,
                "characterPrompts": [],
                "v4_prompt": {
                    "caption": {"base_caption": prompt, "char_captions": []},
                    "use_coords": False,
                    "use_order": True,
                },
                "v4_negative_prompt": {
                    "caption": {"base_caption": merged_negative, "char_captions": []},
                    "legacy_uc": False,
                },
                "negative_prompt": merged_negative,
                "reference_image_multiple": [],
                "reference_information_extracted_multiple": [],
                "reference_strength_multiple": [],
            })
        else:
            parameters["negative_prompt"] = merged_negative

        return {
            "input": prompt,
            "model": model,
            "action": "infill",
            "parameters": parameters,
            "use_new_shared_trial": True,
        }

    async def upscale_image(
        self,
        image_b64: str,
        user_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """图像放大 4 倍。

        Args:
            image_b64: 源图 base64
            user_id: 用户 ID
            width: 源图宽度（不传则自动读取）
            height: 源图高度（不传则自动读取）
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        async def task() -> tuple[bool, str, Optional[str]]:
            return await self._upscale_image_internal(image_b64, user_id, width, height, from_command)
        return await self._enqueue_task(task)

    async def _upscale_image_internal(
        self,
        image_b64: str,
        user_id: str,
        width: Optional[int],
        height: Optional[int],
        from_command: bool,
    ) -> tuple[bool, str, Optional[str]]:
        """图像放大内部实现。"""
        from ..utils.image_utils import ImageUtils

        # 自动读取图片尺寸
        if not width or not height:
            w, h = ImageUtils.get_image_size_from_b64(image_b64)
            width = width or w
            height = height or h

        is_ready, wait_time = self.check_cooldown()
        if not is_ready:
            await asyncio.sleep(wait_time)

        api_key = self._get_current_api_key()
        if not api_key:
            return False, "API Key 没配置", None

        self.last_request_time = time.time()

        if self.channel == "gateway":
            url = f"{self.gateway_base_url}/v1/images/upscale"
            payload = {
                "image": image_b64,
                "width": width,
                "height": height,
                "response_format": "b64_json",
            }
            logger.info(f"[Gateway upscale] {width}x{height} → 4x")
            return await self._gateway_post_and_save(url, payload, api_key, from_command=from_command)

        # official 渠道：POST api.novelai.net/ai/upscale
        url = f"{self.api_base_url}/ai/upscale"
        payload = {
            "image": image_b64,
            "width": width,
            "height": height,
            "scale": 4,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        logger.info(f"[Official upscale] {width}x{height} → 4x")

        try:
            session = await self._get_http_session()
            request_kwargs = self._request_kwargs(
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            )
            async with session.post(url, **request_kwargs) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    return False, f"放大请求失败 ({resp.status}): {err[:200]}", None

                img_data = await resp.read()
                if img_data[:4] == b"PK\x03\x04":
                    return await self._extract_image_from_zip(
                        img_data,
                        from_command=from_command,
                    )
                return await self._save_image_from_bytes(
                    img_data,
                    from_command=from_command,
                )
        except Exception as e:
            logger.error(f"[Official upscale] 异常: {e}", exc_info=True)
            return False, f"放大失败：{e}", None

        return False, "未知错误", None

    async def director_tool(
        self,
        tool_type: str,
        image_b64: str,
        user_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        prompt: Optional[str] = None,
        defry: Optional[int] = None,
        from_command: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """导演工具——对图片进行风格变换或后处理。

        Args:
            tool_type: 工具类型（declutter/bg-removal/lineart/sketch/colorize/emotion）
            image_b64: 源图 base64
            user_id: 用户 ID
            width: 源图宽度
            height: 源图高度
            prompt: 仅 colorize/emotion 需要
            defry: 仅 colorize/emotion 可选，0-5
            from_command: 是否来自命令调用

        Returns:
            (是否成功, 消息, 图片路径或 None)
        """
        async def task() -> tuple[bool, str, Optional[str]]:
            return await self._director_tool_internal(
                tool_type, image_b64, user_id, width, height, prompt, defry, from_command
            )
        return await self._enqueue_task(task)

    async def _director_tool_internal(
        self,
        tool_type: str,
        image_b64: str,
        user_id: str,
        width: Optional[int],
        height: Optional[int],
        prompt: Optional[str],
        defry: Optional[int],
        from_command: bool,
    ) -> tuple[bool, str, Optional[str]]:
        """导演工具内部实现。"""
        from ..utils.image_utils import ImageUtils

        # 自动读取图片尺寸
        if not width or not height:
            w, h = ImageUtils.get_image_size_from_b64(image_b64)
            width = width or w
            height = height or h

        is_ready, wait_time = self.check_cooldown()
        if not is_ready:
            await asyncio.sleep(wait_time)

        api_key = self._get_current_api_key()
        if not api_key:
            return False, "API Key 没配置", None

        self.last_request_time = time.time()

        # gateway 渠道端点映射
        _GW_ENDPOINT_MAP = {
            "declutter": "/v1/images/director-declutter",
            "bg-removal": "/v1/images/director-bg-remover",
            "lineart": "/v1/images/director-lineart",
            "sketch": "/v1/images/director-sketch",
            "colorize": "/v1/images/director-colorize",
            "emotion": "/v1/images/director-emotion",
        }

        if self.channel == "gateway":
            endpoint = _GW_ENDPOINT_MAP.get(tool_type)
            if not endpoint:
                return False, f"未知的导演工具类型: {tool_type}", None
            url = f"{self.gateway_base_url}{endpoint}"
            payload: dict[str, Any] = {
                "image": image_b64,
                "width": width,
                "height": height,
                "response_format": "b64_json",
            }
            if prompt and tool_type in ("colorize", "emotion"):
                payload["prompt"] = prompt
            if defry is not None and tool_type in ("colorize", "emotion"):
                payload["defry"] = max(0, min(5, defry))
            logger.info(f"[Gateway director] {tool_type} | {width}x{height}")
            return await self._gateway_post_and_save(url, payload, api_key, from_command=from_command)

        # official 渠道：POST image.novelai.net/ai/augment-image (JSON 格式)
        url = "https://image.novelai.net/ai/augment-image"
        logger.info(f"[Official augment] {tool_type} | {width}x{height}")

        # 清理 base64 前缀
        clean_b64 = image_b64
        if clean_b64.startswith("data:"):
            clean_b64 = clean_b64.split(",", 1)[-1]

        # JSON 格式请求（文档推荐）
        payload: dict[str, Any] = {
            "req_type": tool_type,
            "image": clean_b64,
            "width": width,
            "height": height,
        }
        if prompt and tool_type in ("colorize", "emotion"):
            payload["prompt"] = prompt
        if defry is not None and tool_type in ("colorize", "emotion"):
            payload["defry"] = max(0, min(5, defry))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            session = await self._get_http_session()
            request_kwargs = self._request_kwargs(
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            )
            async with session.post(url, **request_kwargs) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    return False, f"导演工具请求失败 ({resp.status}): {err[:200]}", None

                img_data = await resp.read()
                if img_data[:4] == b"PK\x03\x04":
                    return await self._extract_image_from_zip(
                        img_data,
                        from_command=from_command,
                    )
                return await self._save_image_from_bytes(
                    img_data,
                    from_command=from_command,
                )
        except Exception as e:
            logger.error(f"[Official augment] 异常: {e}", exc_info=True)
            return False, f"导演工具失败：{e}", None

        return False, "未知错误", None
