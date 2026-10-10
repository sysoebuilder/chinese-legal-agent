"""跨 API/worker 的临时事件通道；不保存或补发流式片段。"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def as_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class RedisEventBus:
    def __init__(self, redis_url: str, *, prefix: str = "agent100:live"):
        self.redis_url = redis_url
        self.prefix = prefix
        self.retry_after = 0.0
        self.publisher = Redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2
        )

    def channel(self, run_id: str) -> str:
        # Pub/Sub 不按 Redis DB 隔离，测试/环境应使用独立前缀。
        return f"{self.prefix}:{run_id}"

    def publish(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        if monotonic() < self.retry_after:
            return
        try:
            self.publisher.publish(
                self.channel(run_id), json.dumps({"event": event, "data": data})
            )
        except RedisError:
            # 展示通道故障不能把已执行的任务标记失败或重复执行节点。
            logger.warning("实时事件暂时无法发送；运行状态仍可从数据库恢复")
            self.retry_after = monotonic() + 5

    @asynccontextmanager
    async def subscribe(self, run_id: str):
        async with (
            AsyncRedis.from_url(self.redis_url, socket_connect_timeout=2) as client,
            client.pubsub() as subscription,
        ):
            await subscription.subscribe(self.channel(run_id))
            # 等待订阅确认后才读取数据库快照，覆盖连接建立期间的状态变化。
            async with asyncio.timeout(5):
                while True:
                    message = await subscription.get_message(timeout=5)
                    if message and message["type"] == "subscribe":
                        break
            yield RedisSubscription(subscription)

    def close(self) -> None:
        self.publisher.close()


class RedisSubscription:
    def __init__(self, subscription):
        self.subscription = subscription

    async def receive(self, timeout: float) -> dict[str, Any] | None:
        message = await self.subscription.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        return json.loads(message["data"]) if message else None
