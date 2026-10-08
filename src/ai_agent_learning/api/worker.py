"""Celery worker：按会话互斥执行，重复消息和过期恢复消息不重复运行。"""

import asyncio
import logging

import psycopg

from ai_agent_learning.api.celery_app import (
    CeleryPublisher,
    celery_app,
    dispatch_pending,
)
from ai_agent_learning.api.errors import exception_diagnostics, public_error
from ai_agent_learning.api.events import RedisEventBus
from ai_agent_learning.api.execution import RunCancelled, execute_agent
from ai_agent_learning.api.runtime import open_worker_agent
from ai_agent_learning.api.settings import ApiSettings
from ai_agent_learning.api.store import PostgresRunStore, RunNotFound
from ai_agent_learning.database import get_database_url, migrate_database

logger = logging.getLogger(__name__)


class RunBusy(Exception):
    pass


async def run_agent(
    store, row, agent_factory, event_bus, cancel_check_interval
) -> None:
    run_id, generation = row["run_id"], row["generation"]
    try:
        async with agent_factory(store.database_url, row["model_config"]) as agent:
            await execute_agent(
                agent,
                store,
                row,
                event_bus,
                cancel_check_interval=cancel_check_interval,
            )
    except RunCancelled:
        await asyncio.to_thread(store.finish, run_id, generation, "cancelled", {})
    except psycopg.Error:
        # 数据库故障不能可靠更新状态，交给 Celery 重新交付。
        raise
    except Exception as error:  # noqa: BLE001 -- 把运行异常转为公开的失败状态。
        # 不把上游错误正文（可能含 Key/URL/工具输出）发送到 SSE。
        logger.error("Agent 运行失败 run_id=%s：%s", run_id, exception_diagnostics(error))
        await asyncio.to_thread(
            store.finish, run_id, generation, "failed", {"error": public_error(error)}
        )


def execute_run(
    store,
    run_id: str,
    generation: int,
    agent_factory=open_worker_agent,
    *,
    event_bus=None,
    cancel_check_interval: float = 1.0,
) -> None:
    try:
        initial = store.get(run_id)
    except RunNotFound:
        return
    if initial["generation"] != generation or initial["status"] not in {
        "queued",
        "running",
    }:
        return
    with store.thread_lock(initial["thread_id"]) as acquired:
        if not acquired:
            raise RunBusy()
        try:
            row = store.begin(run_id, generation)
        except RunNotFound:
            # 读取 initial 后会话可能已被删除；丢弃队列中的旧任务。
            return
        if row is None:
            return

        bus = event_bus or store.event_bus
        asyncio.run(run_agent(store, row, agent_factory, bus, cancel_check_interval))


@celery_app.task(bind=True, name="agent100.execute", max_retries=None)
def execute_task(self, run_id: str, generation: int) -> None:
    try:
        database_url = get_database_url()
        migrate_database(database_url)
        settings = ApiSettings.from_env()
        bus = RedisEventBus(settings.redis_url)
        try:
            execute_run(
                PostgresRunStore(database_url, bus),
                run_id,
                generation,
                cancel_check_interval=settings.cancel_check_interval,
            )
        finally:
            bus.close()
    except (RunBusy, psycopg.Error):
        # 不传异常对象，避免 Celery 重试日志包含连接凭据。
        raise self.retry(countdown=5, max_retries=None) from None


@celery_app.task(name="agent100.dispatch")
def dispatch_task() -> int:
    database_url = get_database_url()
    migrate_database(database_url)
    return dispatch_pending(PostgresRunStore(database_url), CeleryPublisher())
