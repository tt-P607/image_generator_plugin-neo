"""生图任务串行队列。

NovelAI 对并发请求敏感，所有生图/编辑请求都排队串行执行，
配合冷却时间控制避免触发 429 封号。
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import get_task_manager

logger = get_logger("image_generator_plugin.queue")

T = TypeVar("T")

_QueueItem = tuple[Callable[[], Awaitable[object]], "asyncio.Future[object]"]


class SerialTaskQueue:
    """串行执行生图任务的队列。

    队列 worker 由 task_manager 托管，插件卸载时统一取消。
    """

    def __init__(self, *, plugin_name: str, cooldown: int) -> None:
        """初始化队列。

        Args:
            plugin_name: 插件名，写入任务元数据便于排查
            cooldown: 两次请求之间的最小间隔秒数
        """
        self._plugin_name = plugin_name
        self._cooldown = cooldown
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()
        self._worker_task_id: str | None = None
        self._accepting = False
        self._last_request_at = 0.0

    def set_cooldown(self, cooldown: int) -> None:
        """更新冷却时间。

        Args:
            cooldown: 新的间隔秒数
        """
        self._cooldown = cooldown

    def start(self) -> None:
        """启动队列 worker 并开始接单。"""

        self._accepting = True
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        """确保队列 worker 处于运行状态。"""

        task_manager = get_task_manager()
        if self._worker_task_id is not None:
            if not task_manager.get_task(self._worker_task_id).is_done():
                return
            self._worker_task_id = None

        task_info = task_manager.create_task(
            self._run_worker(),
            name="image_generator_queue_worker",
            daemon=True,
            metadata={
                "plugin": self._plugin_name,
                "purpose": "generation_queue_worker",
            },
        )
        self._worker_task_id = task_info.task_id
        logger.info("生图串行任务队列处理器已启动")

    async def _run_worker(self) -> None:
        """依次取出队列任务并执行。"""

        try:
            while True:
                task_func, future = await self._queue.get()
                try:
                    result = await task_func()
                    if not future.done():
                        future.set_result(result)
                except asyncio.CancelledError:
                    if not future.done():
                        future.set_exception(RuntimeError("图片生成任务已取消"))
                    raise
                except Exception as error:
                    logger.error(f"队列任务异常: {error}", exc_info=True)
                    if not future.done():
                        future.set_exception(error)
                finally:
                    self._queue.task_done()
        finally:
            self._worker_task_id = None
            logger.info("生图队列处理器已退出")

    async def submit(self, task_func: Callable[[], Awaitable[T]]) -> T:
        """提交任务并等待其执行结果。

        Args:
            task_func: 无参异步任务

        Returns:
            任务返回值

        Raises:
            RuntimeError: 队列尚未启动或正在关闭
        """
        if not self._accepting:
            raise RuntimeError("图片生成服务尚未初始化或正在卸载")
        self._ensure_worker()

        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        await self._queue.put((task_func, future))  # type: ignore[arg-type]
        logger.info(f"任务已加入队列，当前队列长度: {self._queue.qsize()}")
        return await future  # type: ignore[return-value]

    async def wait_for_cooldown(self) -> None:
        """按冷却时间等待，并记录本次请求时间。"""

        elapsed = time.time() - self._last_request_at
        remaining = self._cooldown - elapsed
        if remaining > 0:
            logger.info(f"需要等待冷却 {int(remaining)} 秒，队列中等待...")
            await asyncio.sleep(remaining)
        self._last_request_at = time.time()

    async def shutdown(self) -> None:
        """停止接单、取消 worker 并唤醒所有等待者。"""

        self._accepting = False
        if self._worker_task_id is not None:
            get_task_manager().cancel_task(self._worker_task_id)
            self._worker_task_id = None

        while not self._queue.empty():
            try:
                _, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(RuntimeError("图片生成插件正在卸载"))
            self._queue.task_done()
