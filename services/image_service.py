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
from typing import TYPE_CHECKING, Any, Optional

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
    使用类级别任务队列串行化所有生图请求，防止 429 封号。
    """

    service_name: str = "image_generator"
    service_description: str = "NovelAI 图片生成服务"
    version: str = "2.0.0"

    # ── 类级别的任务队列和锁，确保所有实例共享同一队列（防止 429 封号）──
    _task_queue: asyncio.Queue[tuple[Any, asyncio.Future[Any]]] = asyncio.Queue()
    _queue_worker_started: bool = False
    _queue_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化服务。

        Args:
            plugin: 所属插件实例
        """
        super().__init__(plugin)

        # 插件目录（通过 __file__ 推算）
        self.plugin_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # 运行时状态
        self.current_key_index: int = 0
        self.last_request_time: float = 0
        self.user_vibes: dict[str, list[dict[str, Any]]] = {}
        self.preset_vibes: list[dict[str, Any]] = []  # 启动时编码好的预设 Vibe
        self.selectable_vibes: dict[str, dict[str, Any]] = {}  # 按名称索引的可选 Vibe 池
        self.manual_vibe_enabled: bool = False
        self.auto_vibe_select: bool = False

        # 配置将在 initialize() 中加载
        self.api_keys: list[str] = []
        self.base_url: str = ""
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
        self.temp_dir: Path = Path()
        self.vibe_storage_dir: Path = Path()
        self.command_images_dir: Path = Path()

    # ═════════════════════════════════════════════════════════════════════
    #  初始化 / 清理
    # ═════════════════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        """初始化服务：加载配置、创建目录、启动队列处理器。"""
        cfg: ImageGeneratorConfig = self.plugin.config  # type: ignore[assignment]

        # API 配置
        self.api_keys = list(cfg.api.api_keys)
        self.base_url = cfg.api.base_url
        self.proxy = cfg.api.proxy
        self.cooldown = cfg.api.cooldown

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

        # 高级参数
        self.max_vibes = cfg.advanced.max_vibes
        self.img2img_default_strength = cfg.advanced.img2img_default_strength
        self.max_characters = cfg.advanced.max_characters

        # 目录
        self.temp_dir = self.plugin_dir / cfg.advanced.temp_dir
        self.vibe_storage_dir = self.plugin_dir / cfg.advanced.vibe_storage_dir
        self.command_images_dir = self.plugin_dir / cfg.advanced.command_images_dir

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.vibe_storage_dir.mkdir(parents=True, exist_ok=True)
        self.command_images_dir.mkdir(parents=True, exist_ok=True)

        # Vibe 配置
        self.always_inject = cfg.vibe.always_enabled
        self.selectable_enabled = cfg.vibe.selectable_enabled

        # 检查配置
        if not self.api_keys:
            logger.warning("未配置 API Key，图片生成功能将不可用")

        # 启动全局任务队列处理器（防 429 封号）
        await self._start_queue_worker()

        # 加载并编码 always Vibe
        if cfg.vibe.always and self.api_keys:
            await self._load_preset_vibes(cfg.vibe.always)

        # 加载并编码 selectable Vibe 池
        if cfg.vibe.selectable and self.api_keys:
            await self._load_selectable_vibes(cfg.vibe.selectable)

        logger.info("图片生成服务初始化完成")

    async def cleanup(self) -> None:
        """清理服务资源。保留缓存文件。"""
        pass

    # ═════════════════════════════════════════════════════════════════════
    #  任务队列（防 429 封号核心机制）
    # ═════════════════════════════════════════════════════════════════════

    async def _start_queue_worker(self) -> None:
        """启动全局任务队列处理器。"""
        async with ImageGeneratorService._queue_lock:
            if not ImageGeneratorService._queue_worker_started:
                ImageGeneratorService._queue_worker_started = True
                get_task_manager().create_task(
                    self._queue_worker(), name="image_generator_queue_worker"
                )
                logger.info("全局生图任务队列处理器已启动（防 429 封号）")

    async def _load_selectable_vibes(self, selectable: list[Any]) -> None:
        """在初始化时加载并编码所有可选 Vibe，结果按名称缓存在 self.selectable_vibes。"""
        self.selectable_vibes = {}
        for item in selectable:
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

    async def _load_preset_vibes(self, presets: list[Any]) -> None:
        """在初始化时加载并编码所有预设 Vibe，结果缓存在 self.preset_vibes."""
        self.preset_vibes = []
        for preset in presets:
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
        """队列处理器：串行处理所有生图任务。"""
        while True:
            task_func = None
            result_future: asyncio.Future[Any] | None = None
            try:
                task_func, result_future = await ImageGeneratorService._task_queue.get()
                result = await task_func()
                if not result_future.done():
                    result_future.set_result(result)
                else:
                    logger.warning("任务结果 future 已完成，跳过设置结果")
            except Exception as e:
                logger.error(f"队列处理器捕获异常: {e}", exc_info=True)
                if result_future is not None and not result_future.done():
                    try:
                        result_future.set_exception(e)
                    except Exception as set_ex:
                        logger.error(f"设置异常失败: {set_ex}", exc_info=True)
            finally:
                if task_func is not None:
                    try:
                        ImageGeneratorService._task_queue.task_done()
                    except Exception as done_ex:
                        logger.error(f"标记任务完成失败: {done_ex}", exc_info=True)
                await asyncio.sleep(0.01)

    async def _enqueue_task(self, task_func: Any) -> Any:
        """将任务加入队列并等待结果。

        Args:
            task_func: 异步任务函数

        Returns:
            任务执行结果
        """
        result_future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        await ImageGeneratorService._task_queue.put((task_func, result_future))
        logger.info(f"任务已加入队列，当前队列长度: {ImageGeneratorService._task_queue.qsize()}")
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
        """调用 /ai/encode-vibe 端点将原始图片编码为 Vibe 向量。

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
        connector = None
        if self.proxy:
            connector = aiohttp.TCPConnector()

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
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
                        logger.error(f"encode-vibe 请求失败 ({resp.status}): {err}")
                        return None
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
                "inpaintImg2ImgStrength": 1,
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
            if not is_img2img:
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
            payload["action"] = "img2img"
            payload["parameters"].update({
                "image": img_base64,
                "strength": strength,
                "noise": 0.0,
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
        """内部生成图片方法（实际执行逻辑）。"""
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

        # 调试日志（隐藏 base64 数据）
        import copy as _copy
        debug_payload = _copy.deepcopy(payload)
        params = debug_payload.get("parameters", {})
        if "image" in params:
            params["image"] = f"<Base64 data, {len(params['image'])} chars>"
        if "reference_image_multiple" in params:
            params["reference_image_multiple"] = [
                f"<Data_{i}, {len(d)} chars>"
                for i, d in enumerate(params["reference_image_multiple"])
            ]
        logger.info(f"请求 payload: {json.dumps(debug_payload, ensure_ascii=False, indent=2)}")

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

        connector = None
        if self.proxy:
            connector = aiohttp.TCPConnector()
            logger.info(f"使用代理: {self.proxy}")

        # 网络级重试（超时/连接错误，最多 2 次重试）
        max_net_retries = 2
        net_retry_delay = 5

        for net_attempt in range(max_net_retries + 1):
            connector = None
            if self.proxy:
                connector = aiohttp.TCPConnector()

            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    request_kwargs: dict[str, Any] = {
                        "json": payload,
                        "headers": headers,
                        "timeout": aiohttp.ClientTimeout(total=60),
                    }
                    if self.proxy:
                        request_kwargs["proxy"] = self.proxy

                    # 429 重试处理
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
                                else:
                                    self._rotate_api_key()
                                    return (
                                        False,
                                        f"请求太频繁了，已经重试了 {attempt} 次，等会儿再试吧",
                                        None,
                                    )

                            if resp.status not in (200, 201):
                                err = await resp.text()
                                try:
                                    import orjson
                                    msg = orjson.loads(err).get("message", err)
                                except Exception:
                                    msg = err
                                return False, f"请求失败了 ({resp.status}): {msg}", None

                            # 检查实际文件头
                            img_data = await resp.read()
                            if img_data[:4] == b"PK\x03\x04":  # ZIP
                                logger.info("检测到 ZIP 格式，开始解压...")
                                return await self._extract_image_from_zip(
                                    img_data, from_command=from_command,
                                )
                            elif img_data[:4] == b"\x89PNG":  # PNG
                                logger.info("检测到 PNG 格式，直接保存...")
                                return await self._save_image_from_bytes(
                                    img_data, from_command=from_command,
                                )
                            else:
                                logger.warning(f"未知文件格式，前 4 字节: {img_data[:4].hex()}")
                                return await self._save_image_from_bytes(
                                    img_data, from_command=from_command,
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
                for filename in zf.namelist():
                    if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        img_data = zf.read(filename)
                        return await self._save_image_from_bytes(
                            img_data, from_command=from_command,
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
            if not img_data or len(img_data) == 0:
                logger.error("图片数据为空")
                return False, "图片数据为空", None

            if img_data[:4] == b"\x89PNG":
                logger.info(f"确认 PNG 格式，大小: {len(img_data)} bytes")
            elif img_data[:4] == b"PK\x03\x04":
                logger.error("这是 ZIP 文件，应该先解压")
                return False, "错误：收到 ZIP 文件但未解压", None
            else:
                logger.warning(f"未知文件格式，前 8 字节: {img_data[:8].hex()}")

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

            # PIL 验证
            try:
                with Image.open(filepath) as img:
                    img.verify()
                with Image.open(filepath) as img:
                    logger.info(f"图片验证成功: {filepath}, 格式: {img.format}, 尺寸: {img.size}")
            except Exception as verify_error:
                logger.warning(f"PIL 验证失败（但文件已保存）: {verify_error}")

            logger.info(f"图片已保存: {filepath}")
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
        # 查找文件（支持模糊匹配）
        final_filename: str | None = None
        exact_path = self.vibe_storage_dir / file_name

        if exact_path.exists():
            final_filename = file_name
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
                candidates = [f for f in all_files if file_name.lower() in f.lower()]

                if len(candidates) == 0:
                    return False, f"素材库中找不到包含 '{file_name}' 的文件"
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

        file_path = self.vibe_storage_dir / final_filename

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

    async def get_user_info(self) -> tuple[bool, str]:
        """NovelAI 官方 API 不提供账号信息查询。"""
        return False, "NovelAI 官方 API 不支持账号信息查询"
