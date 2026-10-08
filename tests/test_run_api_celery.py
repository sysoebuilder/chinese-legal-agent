"""真实 Redis + Celery + PostgreSQL 链路，无真实模型或工具副作用。"""

import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from psycopg import sql
from psycopg.conninfo import make_conninfo
from redis import Redis

from ai_agent_learning.api import worker
from ai_agent_learning.api.app import create_app
from ai_agent_learning.api.celery_app import CeleryPublisher, celery_app
from ai_agent_learning.api.settings import ApiSettings
from ai_agent_learning.api.store import PostgresRunStore
from ai_agent_learning.database import get_database_url, migrate_database
from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer
from ai_agent_learning.model_config import ModelConfig

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_TEST_REDIS") != "1", reason="默认不连接 Redis/Celery"
)


def wait_status(client, run_id, expected):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/v1/runs/{run_id}")
        assert response.status_code == 200
        if response.json()["status"] == expected:
            return response.json()
        if response.json()["status"] == "failed":
            pytest.fail(f"worker 意外失败：{response.json()['error']}")
        time.sleep(0.05)
    pytest.fail(f"运行未达到预期状态 {expected}")


def test_real_celery_delivery_approval_api_restart_and_sse_snapshot(monkeypatch):
    database_url = get_database_url()
    schema = f"api_celery_test_{uuid4().hex}"
    queue = f"agent100-test-{uuid4().hex}"
    prefix = f"{queue}:"
    redis_url = os.getenv("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    redis = Redis.from_url(redis_url)
    assert redis.ping()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated_url = make_conninfo(database_url, options=f"-c search_path={schema}")
    previous = {
        key: celery_app.conf[key]
        for key in ["broker_url", "task_default_queue", "broker_transport_options"]
    }
    effects = []

    @asynccontextmanager
    async def fake_agent(url, model_config):
        def action(state):
            response = interrupt(
                {
                    "action_requests": [
                        {"name": "send_email", "args": {"to": "offline@example.org"}}
                    ]
                }
            )
            effects.append(response)
            return {"messages": [AIMessage(content="真实队列执行完成")]}

        async with open_checkpointer(url) as saver:
            graph = StateGraph(MessagesState)
            graph.add_node("action", action)
            graph.add_edge(START, "action")
            graph.add_edge("action", END)
            yield graph.compile(checkpointer=saver)

    original_execute = worker.execute_run
    monkeypatch.setattr(worker, "get_database_url", lambda: isolated_url)
    monkeypatch.setattr(
        worker,
        "execute_run",
        lambda store, run_id, generation, **kwargs: original_execute(
            store, run_id, generation, fake_agent, **kwargs
        ),
    )
    celery_app.conf.update(
        broker_url=redis_url,
        task_default_queue=queue,
        broker_transport_options={"global_keyprefix": prefix, "visibility_timeout": 60},
    )

    def new_client():
        return TestClient(
            create_app(
                store=PostgresRunStore(isolated_url),
                publisher=CeleryPublisher(celery_app),
                settings=ApiSettings(redis_url=redis_url, dispatch_interval=0.05),
                model_loader=lambda: ModelConfig(
                    "glm", "offline", "not-a-real-key", "https://example.org"
                ),
            )
        )

    try:
        migrate_database(isolated_url)
        with start_worker(
            celery_app,
            pool="solo",
            concurrency=1,
            perform_ping_check=False,
            shutdown_timeout=20,
            loglevel="WARNING",
            queues=[queue],
        ):
            with new_client() as client:
                accepted = client.post("/v1/runs", json={"message": "开始"})
                assert accepted.status_code == 202
                run_id = accepted.json()["run_id"]
                waiting = wait_status(client, run_id, "waiting_approval")
                assert effects == []
                pending_id = waiting["interrupts"][0]["id"]
            # API 已关闭并重新创建，审批和任务仍保存在数据库中。
            with new_client() as client:
                assert (
                    client.get(f"/v1/runs/{run_id}").json()["status"]
                    == "waiting_approval"
                )
                resumed = client.post(
                    f"/v1/runs/{run_id}/resume",
                    json={
                        "responses": {pending_id: {"decisions": [{"type": "approve"}]}},
                    },
                )
                assert resumed.status_code == 202
                completed = wait_status(client, run_id, "succeeded")
                assert completed["answer"] == "真实队列执行完成"
                replay = client.get(
                    f"/v1/runs/{run_id}/events",
                )
                assert "event: interrupt" not in replay.text
                assert "event: run.state" in replay.text
                assert "真实队列执行完成" in replay.text
                # 重复投递已结束及旧代次消息，不能重复副作用。
                publisher = CeleryPublisher(celery_app)
                publisher.publish(run_id, 1)
                publisher.publish(run_id, 2)
                time.sleep(0.5)
                assert len(effects) == 1
    finally:
        celery_app.conf.update(**previous)
        keys = list(redis.scan_iter(match=f"{prefix}*"))
        if keys:
            redis.delete(*keys)
        redis.close()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_real_redis_live_delivery_has_no_replay_and_releases_subscription():
    import asyncio
    from copy import deepcopy
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from ai_agent_learning.api.app import stream_events
    from ai_agent_learning.api.events import RedisEventBus

    redis_url = os.getenv("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = {
        "run_id": run_id,
        "thread_id": str(uuid4()),
        "status": "running",
        "model_config": {"provider": "glm", "model": "offline"},
        "generation": 1,
        "revision": 2,
        "cancel_requested": False,
        "interrupts": [],
        "answer": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    publisher = RedisEventBus(redis_url, prefix=f"test-live-{run_id}")
    subscriber = RedisEventBus(redis_url, prefix=f"test-live-{run_id}")
    store = SimpleNamespace(get=lambda _: deepcopy(row))

    class Connected:
        async def is_disconnected(self):
            return False

    async def scenario():
        # 没有订阅者时发出的片段不能在新连接上重放。
        publisher.publish(
            run_id, "reasoning.delta", {"generation": 1, "text": "旧思考"}
        )
        stream = stream_events(store, Connected(), run_id, subscriber, 15)
        assert '"status": "running"' in await anext(stream)
        for index in range(10):
            publisher.publish(
                run_id, "answer.delta", {"generation": 1, "text": str(index)}
            )
        for index in range(10):
            frame = await asyncio.wait_for(anext(stream), 3)
            assert f'"text": "{index}"' in frame
            assert "旧思考" not in frame and "id:" not in frame
        row.update(status="succeeded", answer="完成", revision=3)
        from ai_agent_learning.api.store import PostgresRunStore

        publisher.publish(
            run_id, "run.state", PostgresRunStore.view(row).model_dump(mode="json")
        )
        assert '"answer": "完成"' in await asyncio.wait_for(anext(stream), 3)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert publisher.publisher.pubsub_numsub(publisher.channel(run_id))[0][1] == 0

    try:
        asyncio.run(scenario())
    finally:
        publisher.close()
        subscriber.close()
