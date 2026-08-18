"""NovelAI HTTP 访问层。

统一封装会话复用、代理注入、网络重试与 429 退避。
上层只描述"请求什么"，不再各自实现重试循环。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("image_generator_plugin.http")

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


class RateLimitedError(Exception):
    """连续遭遇 429 且重试耗尽。"""


class ApiRequestError(Exception):
    """服务端返回了非成功状态码。

    Attributes:
        status: HTTP 状态码
        detail: 服务端返回的错误说明
    """

    def __init__(self, status: int, detail: str) -> None:
        """记录状态码与错误详情。"""

        super().__init__(f"({status}) {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """请求重试策略。

    网络重试同时覆盖网络异常（超时 / 连接错误）与可重试的 5xx
    服务端错误（502 / 503 / 504 等），此类瞬时故障按相同次数与间隔重试。

    Attributes:
        network_attempts: 网络异常及可重试 5xx 时的额外重试次数
        network_delay: 上述重试间隔秒数
        rate_limit_attempts: 遭遇 429 时的额外重试次数
        rate_limit_delay: 429 重试间隔秒数
    """

    network_attempts: int = 2
    network_delay: float = 5.0
    rate_limit_attempts: int = 3
    rate_limit_delay: float = 20.0


def _extract_error_message(body: str) -> str:
    """从错误响应体中提取可读信息。

    Args:
        body: 原始响应文本

    Returns:
        错误说明文本，截断至 500 字符
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]

    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])[:500]
        if isinstance(parsed.get("message"), str):
            return str(parsed["message"])[:500]
    return body[:500]


class NovelAIHttpClient:
    """带重试语义的 NovelAI HTTP 客户端。

    会话在插件生命周期内复用，代理按当前配置注入。
    """

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        """初始化客户端。

        Args:
            policy: 重试策略，缺省使用默认值
        """
        self._session: aiohttp.ClientSession | None = None
        self._proxy: str = ""
        self._policy = policy or RetryPolicy()

    def set_proxy(self, proxy: str) -> None:
        """更新代理地址。

        Args:
            proxy: 代理 URL，空串表示直连
        """
        self._proxy = proxy

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """返回可用会话，必要时重建。"""

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=8),
                timeout=aiohttp.ClientTimeout(total=120),
            )
        return self._session

    async def close(self) -> None:
        """关闭底层会话。"""

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def auth_headers(api_key: str, *, accept: str | None = None) -> dict[str, str]:
        """构造带鉴权的通用请求头。

        Args:
            api_key: NovelAI API Key
            accept: 期望的响应类型，None 表示不声明

        Returns:
            请求头字典
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Origin": "https://novelai.net",
            "Referer": "https://novelai.net",
            "User-Agent": BROWSER_USER_AGENT,
        }
        if accept:
            headers["Accept"] = accept
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
        read_binary: bool,
    ) -> bytes | dict[str, Any]:
        """执行一次带完整重试语义的请求。

        Args:
            method: HTTP 方法
            url: 请求地址
            headers: 请求头
            payload: JSON 请求体，None 表示无请求体
            timeout: 单次请求超时秒数
            read_binary: True 返回原始字节，False 解析为 JSON

        Returns:
            响应字节或已解析的 JSON 对象

        Raises:
            RateLimitedError: 429 重试耗尽
            ApiRequestError: 服务端返回不可重试的非成功状态码
            aiohttp.ClientError: 网络重试耗尽后的底层异常
            asyncio.TimeoutError: 超时重试耗尽
        """
        policy = self._policy
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": aiohttp.ClientTimeout(total=timeout),
        }
        if payload is not None:
            request_kwargs["json"] = payload
        if self._proxy:
            request_kwargs["proxy"] = self._proxy

        last_error: BaseException | None = None

        for net_attempt in range(policy.network_attempts + 1):
            try:
                session = await self._ensure_session()
                for rl_attempt in range(policy.rate_limit_attempts + 1):
                    async with session.request(method, url, **request_kwargs) as response:
                        if response.status == 429:
                            if rl_attempt < policy.rate_limit_attempts:
                                logger.warning(
                                    f"遇到 429，{policy.rate_limit_delay:.0f}s 后重试 "
                                    f"({rl_attempt + 1}/{policy.rate_limit_attempts})"
                                )
                                await asyncio.sleep(policy.rate_limit_delay)
                                continue
                            raise RateLimitedError("请求频率超限")

                        if response.status not in (200, 201):
                            body = await response.text()
                            raise ApiRequestError(
                                response.status,
                                _extract_error_message(body),
                            )

                        if read_binary:
                            return await response.read()
                        return await response.json()

                raise RateLimitedError("请求频率超限")

            except (RateLimitedError, ApiRequestError) as error:
                # 可重试的 5xx（如 502/503/504）属服务端瞬时故障，
                # 与网络异常一样按 network 策略重试，其余错误直接上抛。
                if isinstance(error, ApiRequestError) and 500 <= error.status <= 599:
                    if net_attempt < policy.network_attempts:
                        logger.warning(
                            f"服务端 {error.status}，{policy.network_delay:.0f}s 后重试 "
                            f"({net_attempt + 1}/{policy.network_attempts}): {error.detail}"
                        )
                        await asyncio.sleep(policy.network_delay)
                        continue
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as error:
                last_error = error
                if net_attempt < policy.network_attempts:
                    logger.warning(
                        f"网络异常，{policy.network_delay:.0f}s 后重试 "
                        f"({net_attempt + 1}/{policy.network_attempts}): {error}"
                    )
                    await asyncio.sleep(policy.network_delay)
                    continue
                raise

        raise RuntimeError("HTTP 重试循环异常退出") from last_error

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        api_key: str,
        *,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """POST JSON 请求并解析 JSON 响应。

        Args:
            url: 请求地址
            payload: JSON 请求体
            api_key: NovelAI API Key
            timeout: 超时秒数

        Returns:
            已解析的响应对象
        """
        result = await self._request(
            "POST",
            url,
            headers=self.auth_headers(api_key),
            payload=payload,
            timeout=timeout,
            read_binary=False,
        )
        if not isinstance(result, dict):
            raise ApiRequestError(200, "响应不是 JSON 对象")
        return result

    async def post_binary(
        self,
        url: str,
        payload: dict[str, Any],
        api_key: str,
        *,
        accept: str = "application/zip",
        timeout: float = 120.0,
    ) -> bytes:
        """POST JSON 请求并读取二进制响应。

        Args:
            url: 请求地址
            payload: JSON 请求体
            api_key: NovelAI API Key
            accept: Accept 头
            timeout: 超时秒数

        Returns:
            响应字节
        """
        result = await self._request(
            "POST",
            url,
            headers=self.auth_headers(api_key, accept=accept),
            payload=payload,
            timeout=timeout,
            read_binary=True,
        )
        if not isinstance(result, bytes):
            raise ApiRequestError(200, "响应不是二进制数据")
        return result

    async def get_json(
        self,
        url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """GET 请求并解析 JSON 响应。

        Args:
            url: 请求地址
            api_key: NovelAI API Key
            timeout: 超时秒数

        Returns:
            已解析的响应对象
        """
        result = await self._request(
            "GET",
            url,
            headers=self.auth_headers(api_key),
            payload=None,
            timeout=timeout,
            read_binary=False,
        )
        if not isinstance(result, dict):
            raise ApiRequestError(200, "响应不是 JSON 对象")
        return result

    async def get_binary(
        self,
        url: str,
        api_key: str,
        *,
        timeout: float = 60.0,
    ) -> bytes:
        """GET 请求并读取二进制响应。

        Args:
            url: 请求地址
            api_key: NovelAI API Key
            timeout: 超时秒数

        Returns:
            响应字节
        """
        result = await self._request(
            "GET",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            payload=None,
            timeout=timeout,
            read_binary=True,
        )
        if not isinstance(result, bytes):
            raise ApiRequestError(200, "响应不是二进制数据")
        return result
