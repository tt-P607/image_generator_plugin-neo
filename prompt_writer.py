"""内置提示词写手：把自然语言画面描述写成 NovelAI 标签串。

提供两个入口：
- write_prompt()：共用的写词函数，image_prompt 工具与 draw_image 动作都会调用。
- ImagePromptWriterTool：暴露给聊天模型的 image_prompt 工具（只出标签，不出图）。

写词模型由 [prompt_writer] 配置节指定，角色定义取自 generation.character_prompt，
自定义规范取自 prompt_writer.custom_instructions。
"""
from __future__ import annotations

import re
from typing import Annotated

from src.app.plugin_system.api.llm_api import (
    create_llm_request,
    get_model_set_by_name,
    get_model_set_by_task,
)
from src.app.plugin_system.base import BaseTool
from src.app.plugin_system.types import LLMContextManager, LLMPayload, ROLE, Text
from src.kernel.logger import get_logger

from .config import ImageGeneratorConfig

logger = get_logger("image_generator_plugin.prompt_writer")

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_LABEL_RE = re.compile(r"^(正面提示词|负面提示词|提示词|标签|tags?|prompt)\s*[:：]\s*", re.I)

_SYSTEM = """你是 NovelAI（Danbooru 风格）绘图提示词写手。你的唯一任务：把用户给的画面描述写成一条可直接使用的标签串。

铁律：
1. 只输出标签串本身。英文 tag，以「, 」分隔，一行结束。不要解释、不要序号、不要 markdown 代码块、不要输出中文。
2. 不要写画风与品质标签（例如 masterpiece、best quality、very aesthetic、vivid colors、cel shading 之类）——系统会自动拼在最前面，你写了就是重复。
3. 不要写负面词（lowres、bad anatomy 等），系统另有负面词表。
4. 角色必须使用下面给的定义里的官方词条，一个字都不许改写或增删；同时只写该形态对应的那一条。
5. 覆盖这些维度（缺哪个补哪个）：主体与人数、服装与鞋履、姿势与动作、表情与视线、景别（full body / upper body / close-up）、视角（from below / from above / eye level）、镜头（depth of field）、背景与场景、光线与时段。
6. 拿不准的一律遵循：温柔文静的气质、干净通透的画面、竖构图为常态。
7. 自拍要区分两种情况：普通自拍写 selfie, looking at viewer, arm extended toward viewer, selfie angle，**不要写 holding phone / smartphone / camera / photo frame**（手机正在拍照，画面里不该出现手机）；只有用户明确要求「对镜自拍／镜子自拍」时才写 mirror selfie, holding phone, smartphone, mirror, reflection。
8. 标签总数 25~55 个，具体描述优先于空泛词汇；不要写括号权重。

角色外观定义（第 4 条所指）：
{character}

{extra}"""

_USER = """画面描述：
{description}

{aspect}

请写出这一次的标签串（只输出标签串本身）。"""


def _clean(raw: str) -> str:
    """把模型输出清成一条干净的标签串。"""
    text = (raw or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    text = _LABEL_RE.sub("", text).strip()
    text = text.replace("\r", "\n").replace("\n", ", ")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(,\s*)+", ", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,。")


async def write_prompt(
    config: ImageGeneratorConfig,
    description: str,
    aspect: str = "",
    extra: str = "",
    stream_id: str = "",
) -> tuple[bool, str]:
    """把中文自然语言画面描述写成 NovelAI 标签串。

    成功返回 ``(True, 纯标签串)``；失败返回 ``(False, 给聊天模型的提示)``。
    不包含任何引导语，方便调用方直接拿标签串去用。

    Args:
        config: 已校验的插件配置实例。
        description: 中文画面描述。
        aspect: 画幅倾向（可选）。
        extra: 本次硬性要求（可选）。
        stream_id: 当前聊天流 ID（可选，用于链路追踪）。
    """
    writer = config.prompt_writer
    if not writer.enabled:
        return False, "内置提示词写手已禁用（prompt_writer.enabled=false），先自己写标签吧"

    task_name = (writer.fallback_task_name or "").strip()
    model_name = (writer.model_name or "").strip()
    try:
        if model_name:
            model_set = get_model_set_by_name(
                model_name,
                temperature=writer.temperature,
                max_tokens=writer.max_tokens,
            )
        elif task_name:
            model_set = get_model_set_by_task(task_name)
        else:
            return False, "既没配 prompt_writer.model_name 也没配 fallback_task_name，先自己写标签吧"
    except Exception as exc:
        logger.warning("取写词模型失败（model=%r task=%r）: %s", model_name, task_name, exc)
        return False, (
            "写词模型没取到（检查 config/plugins/image_generator_plugin-neo/config.toml 的 "
            "prompt_writer.model_name 是否是 model.toml 里存在的模型名），先按平时的方式自己写标签吧。"
        )

    extra_block = ""
    custom = writer.custom_instructions.strip()
    if custom:
        extra_block += f"额外要求：\n{custom}\n"
    if extra.strip():
        extra_block += f"本次硬性要求：\n{extra.strip()}\n"

    system_prompt = _SYSTEM.format(
        character=config.generation.character_prompt.strip(),
        extra=extra_block,
    )
    user_prompt = _USER.format(
        description=description.strip(),
        aspect=(f"画幅：{aspect.strip()}" if aspect.strip() else "画幅：按内容自行决定"),
    )

    try:
        request = create_llm_request(
            model_set=model_set,
            request_name="image_prompt_writer",
            context_manager=LLMContextManager(),
            stream_id=stream_id or None,
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, [Text(system_prompt)]))
        request.add_payload(LLMPayload(ROLE.USER, [Text(user_prompt)]))
        response = await request.send(stream=False)
        await response
    except Exception as exc:
        logger.warning("写词模型调用失败: %s", exc)
        return False, f"写词模型调用失败（{exc}），先自己写标签吧"

    tags = _clean(response.message or "")
    if len(tags) < 10:
        logger.warning("写词输出过短或为空: %r", tags)
        return False, "写手模型这次没写出可用的标签，先自己写标签吧"

    logger.info("写手模型完成写词（%d 字符，模型=%s）", len(tags), model_name or task_name or "task")
    return True, tags


class ImagePromptWriterTool(BaseTool):
    """把画面描述写成 NovelAI 标签串（用 [prompt_writer] 指定的模型）。"""

    name = "image_prompt"
    description = (
        "把你想画的画面（中文自然语言即可）交给专门的写手模型，得到可直接用于绘图的 NovelAI 标签串。\n"
        "用法是两步：先用本工具拿到标签，**再把返回的标签原样填入 action-draw_image 的 content_description**"
        "（不要自己改写、增删标签）。\n"
        "如果本工具返回失败，就按平时的方式自己写标签，不要卡住。"
    )

    async def execute(
        self,
        description: Annotated[
            str,
            "画面描述：画的是什么人、在做什么、穿什么、什么场景、什么光线与构图倾向。用中文自然语言写清楚即可。",
        ],
        aspect: Annotated[
            str,
            "画幅倾向：例如「竖图」「横图」「方图」，或留空按内容自动决定。",
        ] = "",
        extra: Annotated[
            str,
            "额外的硬性要求（可选），例如「必须全身可见且露脚」「必须黑丝」「不要背景人物」。",
        ] = "",
    ) -> tuple[bool, str]:
        """把画面描述交给专用写词模型，返回标签串。"""
        plugin = self.plugin
        config = getattr(plugin, "config", None)
        if not isinstance(config, ImageGeneratorConfig):
            return False, "配置未加载，先自己写标签吧"

        stream_id = ""
        try:
            stream_id = self.get_current_stream_id() or ""
        except Exception:
            stream_id = ""

        ok, result = await write_prompt(
            config,
            description.strip(),
            aspect,
            extra,
            stream_id,
        )
        if not ok:
            return False, result

        return True, (
            "写手模型给出的标签如下。请把它**原样**填进 action-draw_image 的 content_description，"
            "不要增删改写标签：\n\n" + result
        )
