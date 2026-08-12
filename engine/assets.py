"""Vibe 与精密参考素材库。

负责从 vibes 目录读取素材、必要时调用 encode-vibe 编码，
并维护 always / selectable / 精密参考三类内存池。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from src.app.plugin_system.api.log_api import get_logger

from ..config import DirectorReferenceItemConfig, VibeItemConfig
from ..media import image_ops
from .settings import EngineSettings
from .types import DirectorRefAsset, VibeAsset

logger = get_logger("image_generator_plugin.assets")

VIBE_FILE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".txt",
    ".json",
    ".naiv4vibe",
    ".naiv4vibebundle",
)
PREENCODED_EXTENSIONS = (".naiv4vibe", ".naiv4vibebundle")
TEXT_EXTENSIONS = (".naiv4vibe", ".naiv4vibebundle", ".json", ".txt")

# NovelAI 导出文件中按模型分组的编码键
MODEL_ENCODING_KEYS: dict[str, str] = {
    "nai-diffusion-4-5": "v4-5full",
    "nai-diffusion-4-5-inpainting": "v4-5full",
    "nai-diffusion-4-curated-preview": "v4",
    "nai-diffusion-4-full": "v4full",
    "nai-diffusion-3": "v3",
}

VibeEncoder = Callable[[str, float], Awaitable[str | None]]


def read_source_image(file_path: Path) -> str:
    """从 Vibe 素材文件中读取原始图片 base64。

    Args:
        file_path: 素材文件路径

    Returns:
        图片 base64，未找到时返回空串
    """
    if file_path.suffix.lower() not in TEXT_EXTENSIONS:
        return image_ops.encode_file(file_path)

    content = file_path.read_text(encoding="utf-8").strip()
    if not content.startswith("{"):
        return content

    data = json.loads(content)
    vibes = data.get("vibes")
    source = vibes[0] if isinstance(vibes, list) and vibes else data
    image = source.get("image", "")
    return image if isinstance(image, str) else ""


def read_preencoded_vector(file_path: Path, model: str) -> str | None:
    """读取 NovelAI 导出文件中已有的 Vibe 编码向量。

    直接复用可避免调用 encode-vibe 端点，不消耗 Anlas。

    Args:
        file_path: 素材文件路径
        model: 当前绘图模型

    Returns:
        编码向量 base64，不存在时返回 None
    """
    if file_path.suffix.lower() not in PREENCODED_EXTENSIONS:
        return None

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(f"读取预编码 Vibe 失败 [{file_path.name}]: {error}")
        return None

    encodings = data.get("encodings")
    if not isinstance(encodings, dict) or not encodings:
        return None

    key = MODEL_ENCODING_KEYS.get(model)
    model_encodings = encodings.get(key) if key else None
    if not model_encodings:
        model_encodings = next(iter(encodings.values()))
    if not isinstance(model_encodings, dict) or not model_encodings:
        return None

    first_entry = next(iter(model_encodings.values()))
    if not isinstance(first_entry, dict):
        return None
    encoding = first_entry.get("encoding")
    return encoding if isinstance(encoding, str) and encoding else None


class AssetLibrary:
    """内存中的素材池。

    always Vibe 每次生图注入，selectable Vibe 由 LLM 按名称选取，
    精密参考同样按名称索引。
    """

    def __init__(self) -> None:
        """初始化空素材池。"""

        self._always_vibes: list[VibeAsset] = []
        self._selectable_vibes: dict[str, VibeAsset] = {}
        self._director_refs: dict[str, DirectorRefAsset] = {}

    @property
    def always_vibes(self) -> tuple[VibeAsset, ...]:
        """始终注入的 Vibe 列表。"""

        return tuple(self._always_vibes)

    @property
    def selectable_vibe_names(self) -> tuple[str, ...]:
        """可供 LLM 选择的 Vibe 名称。"""

        return tuple(self._selectable_vibes)

    @property
    def director_ref_names(self) -> tuple[str, ...]:
        """可供 LLM 选择的精密参考名称。"""

        return tuple(self._director_refs)

    def select_vibes(self, names: Sequence[str]) -> tuple[VibeAsset, ...]:
        """按名称取出可选 Vibe，忽略并记录不存在的名称。

        Args:
            names: LLM 给出的 Vibe 名称

        Returns:
            命中的 Vibe 元组
        """
        selected: list[VibeAsset] = []
        for name in names:
            asset = self._selectable_vibes.get(name)
            if asset is None:
                logger.warning(f"选择了不存在的 Vibe: {name!r}，跳过")
                continue
            selected.append(asset)
        return tuple(selected)

    def select_director_refs(self, names: Sequence[str]) -> tuple[DirectorRefAsset, ...]:
        """按名称取出精密参考，忽略并记录不存在的名称。

        Args:
            names: LLM 给出的参考名称

        Returns:
            命中的参考元组
        """
        selected: list[DirectorRefAsset] = []
        for name in names:
            asset = self._director_refs.get(name)
            if asset is None:
                logger.warning(f"选择了不存在的精密参考: {name!r}，跳过")
                continue
            selected.append(asset)
        return tuple(selected)

    def clear(self) -> None:
        """清空全部素材池。"""

        self._always_vibes.clear()
        self._selectable_vibes.clear()
        self._director_refs.clear()

    async def reload(
        self,
        settings: EngineSettings,
        *,
        always_items: Sequence[VibeItemConfig],
        selectable_items: Sequence[VibeItemConfig],
        director_items: Sequence[DirectorReferenceItemConfig],
        encoder: VibeEncoder,
    ) -> None:
        """按配置重新加载全部素材。

        Args:
            settings: 引擎配置快照
            always_items: always Vibe 配置项
            selectable_items: selectable Vibe 配置项
            director_items: 精密参考配置项
            encoder: Vibe 编码回调，签名为 (图片 base64, IE) -> 向量或 None
        """
        self.clear()
        if not settings.api_keys:
            logger.warning("未配置 API Key，跳过素材加载")
            return

        if settings.vibe_always_enabled:
            loaded = await self._load_vibes(settings, always_items, encoder)
            self._always_vibes = [asset for _, asset in loaded]
            logger.info(f"always Vibe 加载完成，共 {len(self._always_vibes)} 个")

        selectable = await self._load_vibes(settings, selectable_items, encoder)
        self._selectable_vibes = {name: asset for name, asset in selectable}
        logger.info(f"可选 Vibe 池加载完成，共 {len(self._selectable_vibes)} 个")

        self._director_refs = self._load_director_refs(settings, director_items)
        logger.info(f"精密参考池加载完成，共 {len(self._director_refs)} 个")

    async def _load_vibes(
        self,
        settings: EngineSettings,
        items: Sequence[VibeItemConfig],
        encoder: VibeEncoder,
    ) -> list[tuple[str, VibeAsset]]:
        """加载一组 Vibe 配置项。

        Args:
            settings: 引擎配置快照
            items: Vibe 配置项
            encoder: Vibe 编码回调

        Returns:
            (素材名, 素材) 列表，素材名取文件名主干
        """
        assets: list[tuple[str, VibeAsset]] = []
        for item in items:
            if not _is_usable(item):
                continue
            file_path = settings.vibe_storage_dir / item.file
            if not file_path.is_file():
                logger.warning(f"Vibe 文件不存在，跳过: {item.file}")
                continue

            try:
                vector = read_preencoded_vector(file_path, settings.model)
                if vector:
                    logger.info(f"已加载预编码 Vibe（不消耗 Anlas）: {item.file}")
                else:
                    source = read_source_image(file_path)
                    if not source:
                        logger.warning(f"Vibe 文件读取为空，跳过: {item.file}")
                        continue
                    vector = await encoder(source, item.ie)
                    if not vector:
                        logger.warning(f"Vibe 编码失败，跳过: {item.file}")
                        continue
                    logger.info(f"已编码 Vibe: {item.file}")
            except (OSError, ValueError, json.JSONDecodeError) as error:
                logger.error(f"加载 Vibe 失败 [{item.file}]: {error}")
                continue

            name = Path(item.file).stem
            assets.append(
                (
                    name,
                    VibeAsset(
                        data=vector,
                        information_extracted=item.ie,
                        strength=item.strength,
                        name=name,
                    ),
                )
            )
        return assets

    def _load_director_refs(
        self,
        settings: EngineSettings,
        items: Sequence[DirectorReferenceItemConfig],
    ) -> dict[str, DirectorRefAsset]:
        """加载精密参考图并统一裁剪为 API 要求的尺寸。"""

        refs: dict[str, DirectorRefAsset] = {}
        for item in items:
            if not item.enabled or not item.file.strip():
                continue
            file_path = settings.vibe_storage_dir / item.file
            if not file_path.is_file():
                logger.warning(f"精密参考文件不存在，跳过: {item.file}")
                continue

            try:
                source = read_source_image(file_path)
                if not source:
                    continue
                data = image_ops.fit_for_director_reference(source)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                logger.error(f"加载精密参考失败 [{item.file}]: {error}")
                continue

            name = item.name or Path(item.file).stem
            refs[name] = DirectorRefAsset(
                data=data,
                ref_type=item.type,
                fidelity=item.fidelity,
                strength=item.strength,
            )
            logger.info(f"已加载精密参考: {name} ({item.file})")
        return refs


def _is_usable(item: VibeItemConfig) -> bool:
    """判断 Vibe 配置项是否启用且填写了文件名。"""

    return item.enabled and bool(item.file.strip())


def list_vibe_files(storage_dir: Path) -> list[str]:
    """列出素材库中所有支持的文件名。

    Args:
        storage_dir: Vibe 素材目录

    Returns:
        文件名列表
    """
    if not storage_dir.is_dir():
        return []
    return [
        entry.name
        for entry in storage_dir.iterdir()
        if entry.is_file() and entry.suffix.lower() in VIBE_FILE_EXTENSIONS
    ]


def resolve_vibe_file(storage_dir: Path, requested: str) -> tuple[str | None, str]:
    """在素材库中定位文件，支持模糊匹配。

    Args:
        storage_dir: Vibe 素材目录
        requested: 用户输入的文件名

    Returns:
        (命中的文件名或 None, 说明文本)
    """
    name = requested.strip()
    normalized = name.replace("\\", "/")
    if not name or "/" in normalized or Path(normalized).name != normalized:
        return None, "Vibe 文件名不合法"

    if (storage_dir / name).is_file():
        return name, ""

    candidates = [
        entry for entry in list_vibe_files(storage_dir) if name.lower() in entry.lower()
    ]
    if not candidates:
        return None, f"素材库中找不到包含 '{name}' 的文件"
    if len(candidates) == 1:
        return candidates[0], ""

    preview = "\n".join(f"• {item}" for item in candidates[:5])
    message = f"找到 {len(candidates)} 个类似文件，请提供更精确的名称:\n{preview}"
    if len(candidates) > 5:
        message += f"\n...等共 {len(candidates)} 个"
    return None, message
