import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

RETRYABLE_STATUS_CODES = {
    408,  # 请求超时
    429,  # 请求过多
    500,  # 服务器内部错误
    502,  # 网关错误
    503,  # 服务暂时不可用
    504,  # 网关超时
}

T = TypeVar("T")


def is_retryable_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    ):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code in RETRYABLE_STATUS_CODES

    return False


async def wait_before_retry(
    attempt: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> float:
    delay_limit = min(
        max_delay,
        base_delay * 2**attempt,
    )

    delay = random.uniform(0, delay_limit)

    await asyncio.sleep(delay)

    return delay


async def retry_async(
    operation: Callable[[], Awaitable[T]], max_attempts: int = 3
) -> T:
    if max_attempts <= 0:
        raise ValueError("max_attempts 必须大于 0")

    attempt = 0

    while True:
        try:
            attempt += 1
            return await operation()
        except Exception as error:
            if not is_retryable_error(error):
                raise

            if attempt >= max_attempts:
                raise

            await wait_before_retry(attempt - 1)
