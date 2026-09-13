"""NovelAI Enhance 尺寸与提示词规划。"""

from __future__ import annotations

from typing import Literal

EnhanceScale = Literal["1x", "1.5x", "2x", "Max"]

MAX_TARGET_AREA = 3_145_728
MAX_SOURCE_AREA_FOR_MAX = 2_516_582.4
MODEL_ALIGNMENT = 64
_ENHANCE_PROMPT_SUFFIX = ", -2::upscaled, blurry::,"
_TEXT_TAG_PATTERN = "text:"
_NUMERIC_SCALES: tuple[tuple[EnhanceScale, float], ...] = (
    ("1x", 1.0),
    ("1.5x", 1.5),
    ("2x", 2.0),
)


def _nearest_aligned(value: float) -> int:
    """把尺寸四舍五入到最近的 64 倍数。"""

    return max(MODEL_ALIGNMENT, int(value / MODEL_ALIGNMENT + 0.5) * MODEL_ALIGNMENT)


def available_scales(
    width: int,
    height: int,
    *,
    supports_max: bool,
) -> tuple[EnhanceScale, ...]:
    """返回官网 Enhance 面板按显示顺序提供的倍率。"""

    if width <= 0 or height <= 0:
        return ()
    area = width * height
    numeric: list[EnhanceScale]
    if (width, height) in ((832, 1216), (1216, 832)):
        numeric = ["1x", "1.5x"]
    else:
        numeric = [
            scale
            for scale, factor in _NUMERIC_SCALES
            if area * factor**2 <= MAX_TARGET_AREA
            and (width * factor) % MODEL_ALIGNMENT == 0
            and (height * factor) % MODEL_ALIGNMENT == 0
        ]
    if supports_max and area < MAX_SOURCE_AREA_FOR_MAX:
        numeric.append("Max")
    return tuple(numeric)


def pipeline_dimensions(
    width: int,
    height: int,
    scale: EnhanceScale,
) -> tuple[int, int]:
    """计算 Enhance 最终发送到生成管线的 64 对齐尺寸。"""

    if width <= 0 or height <= 0:
        raise ValueError("Enhance 源图尺寸必须为正数")
    if scale == "Max":
        return _nearest_aligned(width), _nearest_aligned(height)
    factors: dict[EnhanceScale, float] = {
        "1x": 1.0,
        "1.5x": 1.5,
        "2x": 2.0,
        "Max": 1.0,
    }
    factor = factors[scale]
    return _nearest_aligned(int(width * factor)), _nearest_aligned(int(height * factor))


def append_prompt_suffix(prompt: str) -> str:
    """按官网规则为普通 Enhance 插入抗模糊提示词片段。"""

    if "upscaled, blurry" in prompt:
        return prompt
    index = prompt.lower().find(_TEXT_TAG_PATTERN)
    if index < 0:
        return f"{prompt}{_ENHANCE_PROMPT_SUFFIX}"
    return f"{prompt[:index]}{_ENHANCE_PROMPT_SUFFIX}{prompt[index:]}"
