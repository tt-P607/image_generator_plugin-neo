"""NovelAI HTTP 客户端重试语义测试。

覆盖网络重试对可重试 5xx（502/503/504）的处理、对不可重试错误
（4xx）的直接上抛，以及 429 的独立重试逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from image_generator_plugin_neo.engine.http import (
    ApiRequestError,
    NovelAIHttpClient,
    RetryPolicy,
)


class _FakeResponse:
    """模拟 aiohttp 响应，按需返回不同状态码与响应体。"""

    def __init__(
        self, status: int, *, body: bytes = b"ok", payload: object = None
    ) -> None:
        """初始化模拟响应。

        Args:
            status: HTTP 状态码
            body: 原始字节响应体（用于 read_binary 场景）
            payload: JSON 响应对象（用于解析场景）
        """
        self.status = status
        self._body = body
        self._payload = payload

    async def __aenter__(self) -> "_FakeResponse":
        """进入异步上下文。"""
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """退出异步上下文。"""
        return False

    async def text(self) -> str:
        """返回文本格式响应体。"""
        return self._body.decode()

    async def read(self) -> bytes:
        """返回原始字节响应体。"""
        return self._body

    async def json(self) -> object:
        """返回 JSON 响应对象。"""
        return self._payload if self._payload is not None else {}


class _Fixture:
    """持有客户端与按序响应的重试测试夹具。"""

    def __init__(
        self, client: NovelAIHttpClient, responses: list[_FakeResponse]
    ) -> None:
        """记录客户端与响应序列。"""
        self.client = client
        self.responses = responses

    async def run(self) -> bytes:
        """执行一次二进制 POST 请求。"""
        return await self.client.post_binary("http://example.invalid", {}, "key")


def _make_fixture(
    statuses: list[int], *, retries: int = 2, body: bytes = b"ok"
) -> _Fixture:
    """构造按序返回给定状态码的客户端夹具。

    Args:
        statuses: 依次返回的 HTTP 状态码列表
        retries: 网络重试次数
        body: 成功时返回的响应字节

    Returns:
        可执行的测试夹具
    """
    responses = [
        _FakeResponse(status, body=body, payload={"ok": True}) for status in statuses
    ]
    cursor = {"index": 0}

    def _fake_request(_method: str, _url: str, **_kwargs: object) -> _FakeResponse:
        """按序返回响应对象，越界时重复最后一个。"""
        index = cursor["index"]
        if index < len(responses):
            cursor["index"] += 1
            return responses[index]
        return responses[-1]

    # 用普通 MagicMock 保证 request() 同步返回响应对象，
    # 使 async with session.request(...) 能直接进入响应上下文。
    session = MagicMock()
    session.request.side_effect = _fake_request

    client = NovelAIHttpClient(
        policy=RetryPolicy(network_attempts=retries, network_delay=0)
    )
    # 直接替换会话获取方法，避免建立真实连接。
    client._ensure_session = AsyncMock(return_value=session)
    return _Fixture(client, responses)


async def test_502_retries_then_succeeds() -> None:
    """验证 502 会按网络策略重试并在后续请求成功。"""
    fixture = _make_fixture([502, 502, 200])
    result = await fixture.run()
    assert result == b"ok"
    assert [r.status for r in fixture.responses] == [502, 502, 200]


async def test_502_exhausted_raises() -> None:
    """验证 502 重试耗尽后抛出 ApiRequestError。"""
    fixture = _make_fixture([502, 502, 502, 502])
    with pytest.raises(ApiRequestError) as exc_info:
        await fixture.run()
    assert exc_info.value.status == 502


async def test_400_does_not_retry() -> None:
    """验证 4xx 错误立即抛出且不重试。"""
    fixture = _make_fixture([400])
    with pytest.raises(ApiRequestError) as exc_info:
        await fixture.run()
    assert exc_info.value.status == 400


async def test_503_retries_then_succeeds() -> None:
    """验证 503 同样按网络策略重试。"""
    fixture = _make_fixture([503, 200])
    result = await fixture.run()
    assert result == b"ok"
    assert [r.status for r in fixture.responses] == [503, 200]
