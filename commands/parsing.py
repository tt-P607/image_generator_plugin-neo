"""命令文本解析。

统一处理画幅别名、正负面切分与 ``--flag value`` 形式的可选参数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SIZE_ALIASES: dict[str, tuple[int, int]] = {
    "方": (1024, 1024),
    "方图": (1024, 1024),
    "square": (1024, 1024),
    "横": (1216, 832),
    "横图": (1216, 832),
    "横版": (1216, 832),
    "landscape": (1216, 832),
    "竖": (832, 1216),
    "竖图": (832, 1216),
    "竖版": (832, 1216),
    "portrait": (832, 1216),
}

SUPPORTED_SIZES = {(1216, 832), (832, 1216), (1024, 1024)}
DEFAULT_SIZE = (1024, 1024)

SIZE_LABELS: dict[tuple[int, int], str] = {
    (1024, 1024): "方形",
    (1216, 832): "横向",
    (832, 1216): "竖向",
}


@dataclass(frozen=True, slots=True)
class DrawPreset:
    """文生图快捷预设。

    Attributes:
        size: 画幅
        prefix: 追加到提示词前的固定标签
        suffix: 追加到提示词后的固定标签
    """

    size: tuple[int, int]
    prefix: str
    suffix: str

    def apply(self, prompt: str) -> str:
        """把预设标签拼到提示词上。

        Args:
            prompt: 用户提示词

        Returns:
            拼接后的提示词
        """
        return f"{self.prefix}{prompt}{self.suffix}".strip()


DRAW_PRESETS: dict[str, DrawPreset] = {
    "人物": DrawPreset(
        size=(832, 1216),
        prefix="masterpiece, best quality, 1girl, ",
        suffix=", detailed, beautiful",
    ),
    "风景": DrawPreset(
        size=(1216, 832),
        prefix="masterpiece, landscape, scenery, ",
        suffix=", detailed, high resolution",
    ),
    "头像": DrawPreset(
        size=(1024, 1024),
        prefix="masterpiece, portrait, close-up, ",
        suffix=", detailed face, high quality",
    ),
}

REFERENCE_TYPE_ALIASES: dict[str, str] = {
    "角色": "character",
    "人物": "character",
    "character": "character",
    "char": "character",
    "风格": "style",
    "style": "style",
    "两者": "character&style",
    "全部": "character&style",
    "both": "character&style",
    "all": "character&style",
    "character&style": "character&style",
}

DEFAULT_REFERENCE_TYPE = "character&style"
DEFAULT_REFERENCE_FIDELITY = 1.0
DEFAULT_REFERENCE_STRENGTH = 1.0

# 允许带负号，使非法负值也能被识别并夹紧，而不是残留在提示词里。
_SCALE_FLAG_PATTERN = re.compile(
    r"--scale\s+(-?[\d.]+)|--rescale\s+(-?[\d.]+)",
    re.IGNORECASE,
)
_MODEL_FLAG_PATTERN = re.compile(r"--model\s+(\S+)", re.IGNORECASE)
_STEPS_FLAG_PATTERN = re.compile(r"--steps\s+(\d+)", re.IGNORECASE)
_VARIETY_FLAG_PATTERN = re.compile(
    r"--variety-plus\s+(true|false)",
    re.IGNORECASE,
)
_RENDER_TEXT_FLAG_PATTERN = re.compile(r"--render-text\b", re.IGNORECASE)
_REFERENCE_FLAG_PATTERN = re.compile(
    r"--(?:type|参考类型)\s+(\S+)"
    r"|--fidelity\s+(-?[\d.]+)"
    r"|--strength\s+(-?[\d.]+)",
    re.IGNORECASE,
)
_POSITIVE_KEYWORD = re.compile(r"正面[：:]")
_NEGATIVE_KEYWORD = re.compile(r"负面[：:]")
_EXTRA_SPACES = re.compile(r" {2,}")

EDIT_STRENGTH_MIN = 0.1
EDIT_STRENGTH_MAX = 0.99
DEFAULT_EDIT_PROMPT = "masterpiece, best quality"


@dataclass(frozen=True, slots=True)
class ScaleFlags:
    """从命令文本中提取的引导参数。

    Attributes:
        scale: 引导比例，未指定为 None
        cfg_rescale: 提示词引导重新缩放，未指定为 None
        remainder: 去掉参数后的剩余文本
    """

    scale: float | None
    cfg_rescale: float | None
    remainder: str


@dataclass(frozen=True, slots=True)
class ReferenceFlags:
    """从命令文本中提取的精密参考参数。

    Attributes:
        ref_type: 参考类型
        fidelity: 忠实度
        strength: 参考强度
        remainder: 去掉参数后的剩余文本
    """

    ref_type: str
    fidelity: float
    strength: float
    remainder: str


@dataclass(frozen=True, slots=True)
class GenerationFlags:
    """从命令文本中提取的模型与生成策略参数。"""

    model: str | None
    steps: int | None
    variety_plus: bool | None
    render_text: bool
    remainder: str


def _to_float(value: str | None) -> float | None:
    """把可选文本转成浮点数，失败返回 None。"""

    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _clean(text: str) -> str:
    """压缩多余空格并去除首尾空白。"""

    return _EXTRA_SPACES.sub(" ", text).strip()


def extract_scale_flags(text: str) -> ScaleFlags:
    """提取 ``--scale`` 与 ``--rescale`` 参数。

    Args:
        text: 原始命令正文

    Returns:
        解析结果
    """
    scale: float | None = None
    rescale: float | None = None

    def _capture(match: re.Match[str]) -> str:
        nonlocal scale, rescale
        parsed_scale = _to_float(match.group(1))
        if parsed_scale is not None:
            scale = parsed_scale
        parsed_rescale = _to_float(match.group(2))
        if parsed_rescale is not None:
            rescale = parsed_rescale
        return ""

    remainder = _clean(_SCALE_FLAG_PATTERN.sub(_capture, text))
    return ScaleFlags(scale=scale, cfg_rescale=rescale, remainder=remainder)


def extract_generation_flags(text: str) -> GenerationFlags:
    """提取模型、步数、Variety+ 与画面文字开关。"""

    model_match = _MODEL_FLAG_PATTERN.search(text)
    steps_match = _STEPS_FLAG_PATTERN.search(text)
    variety_match = _VARIETY_FLAG_PATTERN.search(text)
    render_text = _RENDER_TEXT_FLAG_PATTERN.search(text) is not None

    remainder = _MODEL_FLAG_PATTERN.sub("", text)
    remainder = _STEPS_FLAG_PATTERN.sub("", remainder)
    remainder = _VARIETY_FLAG_PATTERN.sub("", remainder)
    remainder = _RENDER_TEXT_FLAG_PATTERN.sub("", remainder)

    return GenerationFlags(
        model=model_match.group(1) if model_match else None,
        steps=int(steps_match.group(1)) if steps_match else None,
        variety_plus=(
            variety_match.group(1).lower() == "true"
            if variety_match
            else None
        ),
        render_text=render_text,
        remainder=_clean(remainder),
    )


def extract_reference_flags(text: str) -> ReferenceFlags:
    """提取精密参考相关参数。

    Args:
        text: 原始命令正文

    Returns:
        解析结果
    """
    ref_type = DEFAULT_REFERENCE_TYPE
    fidelity = DEFAULT_REFERENCE_FIDELITY
    strength = DEFAULT_REFERENCE_STRENGTH

    def _capture(match: re.Match[str]) -> str:
        nonlocal ref_type, fidelity, strength
        if match.group(1) is not None:
            ref_type = REFERENCE_TYPE_ALIASES.get(
                match.group(1).strip(),
                DEFAULT_REFERENCE_TYPE,
            )
        parsed_fidelity = _to_float(match.group(2))
        if parsed_fidelity is not None:
            fidelity = max(0.0, min(1.0, parsed_fidelity))
        parsed_strength = _to_float(match.group(3))
        if parsed_strength is not None:
            strength = max(0.0, min(1.0, parsed_strength))
        return ""

    remainder = _clean(_REFERENCE_FLAG_PATTERN.sub(_capture, text))
    return ReferenceFlags(
        ref_type=ref_type,
        fidelity=fidelity,
        strength=strength,
        remainder=remainder,
    )


def split_prompt(text: str) -> tuple[str, str | None]:
    """按"正面/负面"关键字切分提示词，兼容半角与全角冒号。

    Args:
        text: 提示词文本

    Returns:
        (正面提示词, 负面提示词或 None)
    """
    negative_match = _NEGATIVE_KEYWORD.search(text)
    if negative_match:
        positive_part = text[: negative_match.start()]
        negative = text[negative_match.end() :].strip()
        positive_match = _POSITIVE_KEYWORD.search(positive_part)
        if positive_match:
            positive_part = positive_part[positive_match.end() :]
        return positive_part.strip(), negative or None

    positive_match = _POSITIVE_KEYWORD.search(text)
    if positive_match:
        return text[positive_match.end() :].strip(), None
    return text.strip(), None


def parse_size_token(token: str) -> tuple[int, int] | None:
    """解析画幅词元，支持中文别名与 ``1024x1024`` 写法。

    Args:
        token: 首个参数词元

    Returns:
        命中的画幅；不是画幅写法时返回 None
    """
    alias = SIZE_ALIASES.get(token.lower())
    if alias is not None:
        return alias

    normalized = token.replace("×", "x").replace("*", "x").lower()
    parts = normalized.split("x")
    if len(parts) != 2:
        return None
    try:
        size = (int(parts[0].strip()), int(parts[1].strip()))
    except ValueError:
        return None
    return size


def parse_edit_args(args: list[str]) -> tuple[str, float | None]:
    """从图生图参数中分离提示词与重绘强度。

    Args:
        args: 空格切分后的参数

    Returns:
        (提示词, 强度或 None)
    """
    prompt_parts: list[str] = []
    strength: float | None = None

    for arg in args:
        value = _to_float(arg)
        if value is not None and EDIT_STRENGTH_MIN <= value <= EDIT_STRENGTH_MAX:
            strength = value
        else:
            prompt_parts.append(arg)

    return " ".join(prompt_parts) or DEFAULT_EDIT_PROMPT, strength
