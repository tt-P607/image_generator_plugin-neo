"""插件后台任务样板。

生图耗时较长，Action 与 Command 都需要把实际工作交给 task_manager 托管，
并在插件卸载时统一取消。本模块封装这套样板。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import get_task_manager

if TYPE_CHECKING:
    from .plugin import ImageGeneratorPlugin

logger = get_logger("image_generator_plugin.background")

T = TypeVar("T")

PLUGIN_NAME = "image_generator_plugin-neo"


def spawn(
    plugin: "ImageGeneratorPlugin",
    coro: Awaitable[T],
    *,
    name: str,
    purpose: str,
    stream_id: str,
) -> asyncio.Task[T] | None:
    """提交一个由插件托管的后台任务。

    Args:
        plugin: 所属插件实例
        coro: 待执行的协程
        name: 任务名
        purpose: 任务用途，写入元数据便于排查
        stream_id: 关联的聊天流 ID

    Returns:
        已启动的 asyncio 任务，创建失败时返回 None
    """
    task_info = get_task_manager().create_task(
        coro,
        name=name,
        metadata={
            "plugin": PLUGIN_NAME,
            "purpose": purpose,
            "stream_id": stream_id,
        },
    )
    plugin.register_background_task(task_info.task_id)

    task = task_info.task
    if task is None:
        plugin.discard_background_task(task_info.task_id)
        return None

    task.add_done_callback(
        lambda _task, task_id=task_info.task_id: plugin.discard_background_task(task_id)
    )
    return task


async def run_shielded(
    plugin: "ImageGeneratorPlugin",
    factory: Callable[[], Awaitable[T]],
    *,
    name: str,
    purpose: str,
    stream_id: str,
    on_detach: Callable[[], None] | None = None,
) -> T:
    """在后台任务中执行工作并等待结果，等待被取消时任务继续跑完。

    调用方（如 LLM 工具调度）可能中途取消等待，但生图请求已经发出，
    此时任务转入后台继续执行，由业务自行补偿通知用户。

    Args:
        plugin: 所属插件实例
        factory: 返回待执行协程的工厂
        name: 任务名
        purpose: 任务用途
        stream_id: 关联的聊天流 ID
        on_detach: 等待被取消时的回调，用于标记"需要补偿通知"

    Returns:
        任务执行结果

    Raises:
        RuntimeError: 后台任务创建失败
        asyncio.CancelledError: 等待被取消，任务已转入后台
    """
    task = spawn(
        plugin,
        factory(),
        name=name,
        purpose=purpose,
        stream_id=stream_id,
    )
    if task is None:
        raise RuntimeError("后台任务创建失败")

    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_detach is not None:
            on_detach()
        logger.warning(f"任务 {name} 的等待被取消，转入后台继续执行")
        raise
