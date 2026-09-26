"""在模型请求中注入本插件生成的图片。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.event_api import EventDecision
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.media_api import get_media_file
from src.app.plugin_system.base import BaseAction, BaseEventHandler
from src.app.plugin_system.types import EventType, Image, LLMPayload, ROLE, Text, ToolResult

from .media import pending_generated_images

_INJECTED_IDS_KEY = "image_generator_injected_media_ids"
_MAX_IMAGES_PER_REQUEST = 3
logger = get_logger("image_generator_plugin.generated_image_context")


class GeneratedImageContextHandler(BaseEventHandler):
    """将成功发送的生成图注入对应聊天流的下一次模型请求。"""

    name = "generated_image_context"
    description = "将生成图片作为多模态输入注入模型请求"
    weight = -80
    init_subscribe = [EventType.BEFORE_LLM_REQUEST, EventType.AFTER_LLM_REQUEST]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """请求前注入原图，成功响应后确认待注入媒体 ID。"""
        config = self.plugin.image_config.plugin
        if not config.enabled or not config.inject_generated_image or config.output_image_context != "base64":
            return EventDecision.PASS, params

        metadata = params.get("meta_data")
        if not isinstance(metadata, dict):
            return EventDecision.PASS, params
        stream_id = metadata.get("stream_id")
        if not isinstance(stream_id, str) or not stream_id:
            return EventDecision.PASS, params

        pending = pending_generated_images.get(stream_id)
        if event_name == EventType.AFTER_LLM_REQUEST:
            if params.get("success") is True:
                injected_ids = metadata.pop(_INJECTED_IDS_KEY, ())
                if pending:
                    pending[:] = [media_id for media_id in pending if media_id not in injected_ids]
                    if not pending:
                        pending_generated_images.pop(stream_id, None)
            return EventDecision.PASS, params

        if event_name != EventType.BEFORE_LLM_REQUEST or not pending:
            return EventDecision.PASS, params

        payloads = params.get("payloads")
        if not isinstance(payloads, list):
            return EventDecision.PASS, params
        actions = {
            component for component in self.plugin.get_components()
            if issubclass(component, BaseAction)
        }
        tools = params.get("tools")
        has_own_action = isinstance(tools, list) and any(tool in actions for tool in tools)
        if not has_own_action and not self._references_image(payloads, pending):
            return EventDecision.PASS, params

        content = []
        injected_ids = []
        for media_id in pending[:_MAX_IMAGES_PER_REQUEST]:
            image_data = await get_media_file(media_id)
            if image_data is not None:
                content.extend((Text(f"[图片({media_id})]"), Image(image_data)))
                injected_ids.append(media_id)
            else:
                logger.warning(f"生成图片媒体缓存不可回查: {media_id}")
                pending.remove(media_id)
        if not pending:
            pending_generated_images.pop(stream_id, None)
        if not injected_ids:
            return EventDecision.PASS, params

        params["payloads"] = [*payloads, LLMPayload(ROLE.USER, content)]
        metadata[_INJECTED_IDS_KEY] = tuple(injected_ids)
        return EventDecision.SUCCESS, params

    @staticmethod
    def _references_image(payloads: list[LLMPayload], pending: list[str]) -> bool:
        """检查请求正文或工具结果是否引用了等待注入的生成图。"""
        for payload in payloads:
            if payload.role not in (ROLE.USER, ROLE.TOOL_RESULT):
                continue
            for part in payload.content:
                value = part.text if isinstance(part, Text) else (
                    part.value if isinstance(part, ToolResult) else None
                )
                if isinstance(value, str) and any(
                    f"[图片({media_id})]" in value or media_id in value
                    for media_id in pending
                ):
                    return True
        return False