"""无真实模型、Redis 或数据库的 HTTP、SSE 和 checkpoint 恢复测试。"""

import asyncio
import queue
import threading
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

from chinese_legal_agent.api.app import create_app, stream_events
from chinese_legal_agent.api.celery_app import create_celery, dispatch_pending
from chinese_legal_agent.api.events import as_sse
from chinese_legal_agent.api.execution import CancellationGuard, RunCancelled
from chinese_legal_agent.api.schemas import validate_responses
from chinese_legal_agent.api.settings import ApiSettings
from chinese_legal_agent.api.store import RunConflict, RunNotFound
from chinese_legal_agent.api.worker import RunBusy, execute_run
from chinese_legal_agent.model_config import ModelConfig

MODEL = ModelConfig("glm", "test-model", "private-model-key", "https://model.example")
PUBLIC_MODEL = {
    "provider": MODEL.provider,
    "model": MODEL.model,
    "base_url": MODEL.base_url,
}


class MemoryEventBus:
    """测试通道仅向当前订阅者广播；published 只用于断言，不用于补发。"""

    def __init__(self):
        self.published = []
        self.subscriptions = {}
        self.lock = threading.Lock()

    def publish(self, run_id, event, data):
        message = {"event": event, "data": deepcopy(data)}
        with self.lock:
            self.published.append((run_id, message))
            for subscriber in self.subscriptions.get(run_id, []):
                subscriber.put(message)

    @asynccontextmanager
    async def subscribe(self, run_id):
        messages = queue.Queue()
        with self.lock:
            self.subscriptions.setdefault(run_id, []).append(messages)

        class Subscription:
            async def receive(self, timeout):
                def read():
                    try:
                        return messages.get(timeout=timeout)
                    except queue.Empty:
                        return None

                return await asyncio.to_thread(read)

        try:
            yield Subscription()
        finally:
            with self.lock:
                self.subscriptions[run_id].remove(messages)


class FakeStore:
    database_url = "postgresql://fake/offline"

    def __init__(self):
        self.event_bus = MemoryEventBus()
        self.rows = {}
        self.history = {}
        self.locked = False

    def create(self, message, thread_id, model_config):
        if thread_id and any(
            r["thread_id"] == thread_id
            and r["status"] not in {"succeeded", "cancelled"}
            for r in self.rows.values()
        ):
            raise RunConflict("会话有未解决的运行")
        run_id = str(uuid4())
        now = datetime.now(UTC)
        self.rows[run_id] = {
            "run_id": run_id,
            "thread_id": thread_id or str(uuid4()),
            "message": message,
            "model_config": model_config,
            "status": "queued",
            "generation": 1,
            "dispatch_pending": True,
            "cancel_requested": False,
            "interrupts": [],
            "resume_values": {},
            "answer": None,
            "error": None,
            "revision": 0,
            "created_at": now,
            "updated_at": now,
        }
        self.history[run_id] = []
        self._event(run_id, "run.queued", {})
        return self.get(run_id)

    def get(self, run_id):
        if run_id not in self.rows:
            raise RunNotFound(run_id)
        return deepcopy(self.rows[run_id])

    def _event(self, run_id, event, data):
        row = self.rows[run_id]
        row["revision"] += 1
        self.history[run_id].append(SimpleNamespace(event=event, data=data))
        from chinese_legal_agent.api.store import PostgresRunStore

        self.event_bus.publish(
            run_id, "run.state", PostgresRunStore.view(row).model_dump(mode="json")
        )

    def pending_dispatches(self):
        return [
            self.get(r["run_id"])
            for r in self.rows.values()
            if r["dispatch_pending"] and r["status"] == "queued"
        ]

    def dispatched(self, run_id, generation):
        if self.rows[run_id]["generation"] == generation:
            self.rows[run_id]["dispatch_pending"] = False

    @contextmanager
    def thread_lock(self, thread_id):
        yield not self.locked

    def begin(self, run_id, generation):
        row = self.rows[run_id]
        if row["generation"] != generation or row["status"] not in {
            "queued",
            "running",
        }:
            return None
        if row["cancel_requested"]:
            return None
        row.update(status="running", dispatch_pending=False)
        self._event(run_id, "run.running", {})
        return self.get(run_id)

    def interrupted(self, run_id, generation, interrupts):
        row = self.rows[run_id]
        status = (
            "waiting_approval"
            if any("action_requests" in i["value"] for i in interrupts)
            else "waiting_input"
        )
        row.update(status=status, interrupts=interrupts, resume_values={})
        self._event(run_id, "interrupt", {"status": status, "interrupts": interrupts})

    def finish(self, run_id, generation, status, data):
        row = self.rows[run_id]
        if row["status"] != "running" or row["generation"] != generation:
            return
        if row["cancel_requested"]:
            status, data = "cancelled", {}
        row.update(
            status=status,
            answer=data.get("answer"),
            error=data.get("error"),
            interrupts=[],
            resume_values={},
        )
        self._event(run_id, f"run.{status}", data)

    def resume(self, run_id, responses):
        row = self.rows[run_id]
        if row["status"] not in {"waiting_input", "waiting_approval", "failed"}:
            raise RunConflict("不可恢复")
        validate_responses(row["interrupts"], responses)
        row.update(
            status="queued",
            generation=row["generation"] + 1,
            dispatch_pending=True,
            resume_values=responses,
        )
        self._event(run_id, "run.queued", {"resumed": True})
        return self.get(run_id)

    def cancel(self, run_id):
        row = self.rows[run_id]
        if row["status"] not in {"succeeded", "cancelled"}:
            row["cancel_requested"] = True
            if row["status"] != "running":
                row["status"] = "cancelled"
                row["dispatch_pending"] = False
                self._event(run_id, "run.cancelled", {})
        return self.get(run_id)


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, run_id, generation):
        self.published.append((run_id, generation))


def client_for(store, **kwargs):
    return TestClient(
        create_app(
            store=store,
            publisher=FakePublisher(),
            model_loader=lambda: MODEL,
            settings=ApiSettings(
                heartbeat_interval=0.02, dispatch_interval=0.01, **kwargs
            ),
            event_bus=store.event_bus,
        )
    )


def factory_for(agent):
    @asynccontextmanager
    async def factory(database_url, model_config):
        assert model_config == PUBLIC_MODEL
        yield agent

    return factory


def test_create_run_is_accepted_without_running_agent_or_exposing_credentials():
    store = FakeStore()
    with client_for(store) as client:
        response = client.post("/v1/runs", json={"message": "  你好  "})
        assert response.status_code == 202
        view = response.json()
        assert view["status"] == "queued"
        assert view["model"] == "test-model"
        assert "private-model-key" not in response.text
        assert store.get(view["run_id"])["message"] == "你好"
        assert "private-model-key" not in repr(store.rows)
        assert client.get(f"/v1/runs/{view['run_id']}").status_code == 200
        assert (
            client.post(
                "/v1/runs", json={"message": "下一句", "thread_id": view["thread_id"]}
            ).status_code
            == 409
        )
        assert client.post("/v1/runs", json={"message": "  "}).status_code == 422
        assert client.get(f"/v1/runs/{uuid4()}").status_code == 404


def test_all_run_endpoints_require_configured_token():
    store = FakeStore()
    run_id = store.create("你好", None, PUBLIC_MODEL)["run_id"]
    with client_for(store, token="private-api-token") as client:
        for method, path, body in [
            ("POST", "/v1/runs", {"message": "你好"}),
            ("GET", f"/v1/runs/{run_id}", None),
            ("GET", f"/v1/runs/{run_id}/events", None),
            ("POST", f"/v1/runs/{run_id}/resume", {}),
            ("GET", f"/v1/sessions/{store.get(run_id)['thread_id']}/recovery", None),
            ("POST", f"/v1/runs/{run_id}/reconnect", {}),
            ("POST", f"/v1/runs/{run_id}/cancel", None),
        ]:
            assert client.request(method, path, json=body).status_code == 401
        assert (
            client.get(
                f"/v1/runs/{run_id}",
                headers={"Authorization": "Bearer private-api-token"},
            ).status_code
            == 200
        )


def test_sse_reconnect_returns_snapshot_without_replaying_deltas():
    store = FakeStore()
    row = store.create("你好", None, PUBLIC_MODEL)
    run_id = row["run_id"]
    store.begin(run_id, 1)
    store.event_bus.publish(run_id, "reasoning.delta", {"text": "先分析\n再回答"})
    store.finish(run_id, 1, "succeeded", {"answer": "你好"})
    with client_for(store) as client:
        response = client.get(f"/v1/runs/{run_id}/events")
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: run.state" in response.text
        assert '"answer": "你好"' in response.text
        assert "reasoning.delta" not in response.text
        assert "id:" not in response.text
        assert response.text.count("event:") == 1
        assert store.get(run_id)["status"] == "succeeded"
    assert 'data: {"text": "先分析\\n再回答"}' in as_sse(
        "reasoning.delta", {"text": "先分析\n再回答"}
    )


def test_disconnect_does_not_cancel_execution():
    store = FakeStore()
    run_id = store.create("你好", None, PUBLIC_MODEL)["run_id"]
    store.begin(run_id, 1)

    class Disconnected:
        async def is_disconnected(self):
            return True

    async def scenario():
        assert [
            event
            async for event in stream_events(
                store, Disconnected(), run_id, store.event_bus, 15
            )
        ] == []

    asyncio.run(scenario())
    assert store.get(run_id)["status"] == "running"
    assert not store.get(run_id)["cancel_requested"]


def test_outbox_survives_failed_publication_and_old_generation_does_not_clear_new_dispatch():
    store = FakeStore()
    run_id = store.create("你好", None, PUBLIC_MODEL)["run_id"]

    class Unavailable:
        def publish(self, *args):
            raise ConnectionError("offline")

    with pytest.raises(ConnectionError):
        dispatch_pending(store, Unavailable())
    assert store.get(run_id)["dispatch_pending"]
    publisher = FakePublisher()
    assert dispatch_pending(store, publisher) == 1
    assert publisher.published == [(run_id, 1)]
    assert dispatch_pending(store, publisher) == 0
    store.rows[run_id].update(generation=2, dispatch_pending=True)
    store.dispatched(run_id, 1)
    assert store.get(run_id)["dispatch_pending"]


def test_celery_uses_json_late_ack_and_redis_broker_without_model_credentials():
    app = create_celery(ApiSettings())
    assert app.conf.broker_url == "redis://127.0.0.1:6379/0"
    assert app.conf.task_acks_late and app.conf.task_reject_on_worker_lost
    assert app.conf.accept_content == ["json"]
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_ignore_result


@pytest.mark.parametrize("kind", ["approval", "information"])
def test_real_checkpoint_interrupt_resume_does_not_repeat_user_input_or_tool(kind):
    effects = []
    if kind == "approval":
        request = {
            "action_requests": [
                {"name": "send_email", "args": {"to": "test@example.org"}}
            ]
        }
        response = {"decisions": [{"type": "approve"}]}
    else:
        request = {"type": "legal_information_required", "question": "发生在哪个城市？"}
        response = {"answer": "上海"}

    def action(state):
        approved = interrupt(request)
        effects.append(approved)
        return {"messages": [AIMessage(content="任务完成")]}

    graph = StateGraph(MessagesState)
    graph.add_node("action", action)
    graph.add_edge(START, "action")
    graph.add_edge("action", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    store = FakeStore()
    row = store.create("开始", None, PUBLIC_MODEL)
    run_id = row["run_id"]
    factory = factory_for(agent)
    execute_run(store, run_id, 1, factory)
    assert effects == []
    waiting = store.get(run_id)
    assert waiting["status"] == (
        "waiting_approval" if kind == "approval" else "waiting_input"
    )
    interrupt_id = waiting["interrupts"][0]["id"]
    # 重复交付等待中的任务不会执行工具或再次提示。
    execute_run(store, run_id, 1, factory)
    assert len(store.history[run_id]) == 3
    with client_for(store) as client:
        assert (
            client.post(
                f"/v1/runs/{run_id}/resume", json={"responses": {"stale": response}}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/v1/runs/{run_id}/resume",
                json={"responses": {interrupt_id: response}},
            ).status_code
            == 202
        )
        assert (
            client.post(
                f"/v1/runs/{run_id}/resume",
                json={"responses": {interrupt_id: response}},
            ).status_code
            == 409
        )
    execute_run(store, run_id, 1, factory)  # 过期消息不得消耗新的审批。
    assert effects == []
    execute_run(store, run_id, 2, factory)
    execute_run(store, run_id, 2, factory)
    assert effects == [response]
    assert store.get(run_id)["answer"] == "任务完成"
    snapshot = asyncio.run(
        agent.aget_state({"configurable": {"thread_id": row["thread_id"]}})
    )
    assert (
        len([m for m in snapshot.values["messages"] if isinstance(m, HumanMessage)])
        == 1
    )


def test_real_checkpoint_completed_before_status_commit_is_not_reexecuted():
    effects = []

    def action(state):
        effects.append("called")
        return {"messages": [AIMessage(content="完成")]}

    graph = StateGraph(MessagesState)
    graph.add_node("action", action)
    graph.add_edge(START, "action")
    graph.add_edge("action", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    store = FakeStore()
    row = store.create("开始", None, PUBLIC_MODEL)
    config = {
        "configurable": {"thread_id": row["thread_id"]},
        "metadata": {"run_id": row["run_id"]},
    }
    asyncio.run(
        agent.ainvoke(
            {
                "messages": [
                    HumanMessage(content="开始", id=f"run:{row['run_id']}:user")
                ]
            },
            config,
        )
    )
    store.rows[row["run_id"]]["status"] = "running"
    execute_run(store, row["run_id"], 1, factory_for(agent))
    assert effects == ["called"]
    assert store.get(row["run_id"])["status"] == "succeeded"


def test_real_checkpoint_failed_node_resumes_without_repeating_completed_node():
    calls = []

    def first(state):
        calls.append("first")
        return {}

    def second(state):
        calls.append("second")
        if calls.count("second") == 1:
            raise RuntimeError("private-upstream-error")
        return {"messages": [AIMessage(content="恢复成功")]}

    graph = StateGraph(MessagesState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    store = FakeStore()
    row = store.create("开始", None, PUBLIC_MODEL)
    execute_run(store, row["run_id"], 1, factory_for(agent))
    assert store.get(row["run_id"])["error"] == "RuntimeError"
    assert "private-upstream-error" not in repr(store.history)
    store.resume(row["run_id"], {})
    execute_run(store, row["run_id"], 2, factory_for(agent))
    assert calls == ["first", "second", "second"]
    assert store.get(row["run_id"])["answer"] == "恢复成功"


def test_stream_transmits_provider_reasoning_and_final_answer_without_terminal_input():
    class Agent:
        completed = False

        async def aget_state(self, config):
            return SimpleNamespace(
                metadata=None,
                interrupts=(),
                next=(),
                values={
                    "messages": [AIMessage(content="最终回答")]
                    if self.completed
                    else [],
                },
            )

        async def astream(self, agent_input, **kwargs):
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(
                        content=[{"type": "reasoning", "reasoning": "模型返回的思考"}],
                        id="m1",
                    ),
                    {"langgraph_node": "model"},
                ),
            }
            yield {
                "type": "messages",
                "data": (AIMessageChunk(content="最终回答", id="m1"), {}),
            }
            self.completed = True

    store = FakeStore()
    row = store.create("开始", None, PUBLIC_MODEL)
    execute_run(store, row["run_id"], 1, factory_for(Agent()))
    events = [message for _, message in store.event_bus.published]
    assert [message["event"] for message in events] == [
        "run.state",
        "run.state",
        "reasoning.delta",
        "answer.delta",
        "run.state",
    ]
    assert events[-1]["data"]["answer"] == "最终回答"
    assert [event.event for event in store.history[row["run_id"]]] == [
        "run.queued",
        "run.running",
        "run.succeeded",
    ]


def test_cancel_blocks_new_model_and_tool_calls_and_queued_task_never_opens_agent():
    store = FakeStore()
    row = store.create("开始", None, PUBLIC_MODEL)
    store.begin(row["run_id"], 1)
    store.cancel(row["run_id"])
    guard = CancellationGuard(store, row["run_id"], 1)

    async def scenario():
        with pytest.raises(RunCancelled):
            await guard.on_chat_model_start({}, [])
        with pytest.raises(RunCancelled):
            await guard.on_tool_start({}, "")

    asyncio.run(scenario())
    # 验证真实 LangChain 模型回调在模型实现执行前传播取消。
    from unittest.mock import patch

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    model = FakeListChatModel(responses=["不应生成"])
    with (
        patch.object(
            FakeListChatModel, "_generate", side_effect=AssertionError("不应调用模型")
        ),
        pytest.raises(RunCancelled),
    ):
        asyncio.run(model.ainvoke("测试", config={"callbacks": [guard]}))
    queued = store.create("另一个任务", None, PUBLIC_MODEL)
    store.cancel(queued["run_id"])

    @asynccontextmanager
    async def forbidden(*args):
        pytest.fail("取消的排队任务不应创建 Agent")
        yield

    execute_run(store, queued["run_id"], 1, forbidden)
    store.locked = True
    active = store.create("互斥任务", None, PUBLIC_MODEL)
    with pytest.raises(RunBusy):
        execute_run(store, active["run_id"], 1, forbidden)


@pytest.mark.parametrize(
    "decision",
    [
        {"type": "edit"},
        {"type": "approve", "args": {}},
        {"type": "reject", "message": 1},
    ],
)
def test_approval_cannot_change_tool_arguments(decision):
    pending = [
        {"id": "i1", "value": {"action_requests": [{"name": "send_email", "args": {}}]}}
    ]
    with pytest.raises(ValueError):
        validate_responses(pending, {"i1": {"decisions": [decision]}})


def test_final_answer_does_not_return_previous_turn_when_current_turn_has_no_answer():
    from chinese_legal_agent.api.execution import final_answer

    snapshot = SimpleNamespace(
        values={
            "messages": [
                HumanMessage(content="上轮"),
                AIMessage(content="上轮回答"),
                HumanMessage(content="本轮"),
            ]
        }
    )
    with pytest.raises(RuntimeError):
        final_answer(snapshot)


class Connected:
    async def is_disconnected(self):
        return False


def test_live_stream_pushes_without_database_reads_per_delta_and_cleans_up():
    store = FakeStore()
    run_id = store.create("开始", None, PUBLIC_MODEL)["run_id"]
    store.begin(run_id, 1)
    reads = []
    original_get = store.get

    def counted_get(key):
        reads.append(key)
        return original_get(key)

    store.get = counted_get

    async def scenario():
        stream = stream_events(store, Connected(), run_id, store.event_bus, 15)
        assert '"status": "running"' in await anext(stream)
        assert len(reads) == 2
        # 订阅后缓冲的旧快照和旧代次片段都不能覆盖当前状态。
        store.event_bus.publish(run_id, "run.state", {"revision": 0})
        store.event_bus.publish(
            run_id, "answer.delta", {"generation": 0, "text": "旧回答"}
        )
        for index in range(100):
            store.event_bus.publish(
                run_id, "answer.delta", {"generation": 1, "text": str(index)}
            )
        for index in range(100):
            assert f'"text": "{index}"' in await anext(stream)
        assert len(reads) == 2
        store.finish(run_id, 1, "succeeded", {"answer": "完成"})
        assert '"answer": "完成"' in await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not store.event_bus.subscriptions[run_id]

    asyncio.run(scenario())


def test_subscription_is_ready_before_snapshot_and_waiting_approval_survives_refresh():
    store = FakeStore()
    run_id = store.create("开始", None, PUBLIC_MODEL)["run_id"]
    store.begin(run_id, 1)
    pending = [{"id": "review", "value": {"action_requests": [{"name": "send_email"}]}}]

    class RacingBus(MemoryEventBus):
        @asynccontextmanager
        async def subscribe(self, key):
            async with super().subscribe(key) as subscription:
                store.interrupted(key, 1, pending)
                yield subscription

    bus = RacingBus()
    store.event_bus = bus

    async def scenario():
        stream = stream_events(store, Connected(), run_id, bus, 15)
        snapshot = await anext(stream)
        assert '"status": "waiting_approval"' in snapshot
        assert '"id": "review"' in snapshot
        await stream.aclose()
        assert not bus.subscriptions[run_id]

    asyncio.run(scenario())
    assert store.get(run_id)["interrupts"] == pending


def test_heartbeat_recovers_committed_state_when_publication_is_lost():
    store = FakeStore()
    run_id = store.create("开始", None, PUBLIC_MODEL)["run_id"]
    store.begin(run_id, 1)

    async def scenario():
        stream = stream_events(store, Connected(), run_id, store.event_bus, 0.01)
        await anext(stream)
        # 模拟事务已提交、worker 尚未 publish 就退出。
        store.rows[run_id].update(status="succeeded", answer="已保存", revision=3)
        assert '"answer": "已保存"' in await anext(stream)
        assert await anext(stream) == ": ping\n\n"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_cancel_check_is_throttled_but_calls_and_completion_force_a_check(monkeypatch):
    from chinese_legal_agent.api import execution

    store = FakeStore()
    run_id = store.create("开始", None, PUBLIC_MODEL)["run_id"]
    clock = [0.0]
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    guard = CancellationGuard(store, run_id, 1)
    reads = []
    original_get = store.get
    store.get = lambda key: (reads.append(key), original_get(key))[1]

    async def scenario():
        for _ in range(100):
            await guard.check()
        assert len(reads) == 1
        clock[0] = 1.0
        await guard.check()
        assert len(reads) == 2
        store.cancel(run_id)
        with pytest.raises(RunCancelled):
            await guard.on_tool_start({}, "")
        with pytest.raises(RunCancelled):
            await guard.check(force=True)

    asyncio.run(scenario())


def test_redis_publish_failure_is_best_effort_and_backs_off(monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    from chinese_legal_agent.api import events

    attempts = []
    clock = [0.0]
    monkeypatch.setattr(events, "monotonic", lambda: clock[0])
    bus = events.RedisEventBus("redis://127.0.0.1:1/0")

    def unavailable(*args):
        attempts.append(args)
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(bus.publisher, "publish", unavailable)
    try:
        for _ in range(100):
            bus.publish("run", "answer.delta", {"generation": 1, "text": "片段"})
        assert len(attempts) == 1
        clock[0] = 5.0
        bus.publish("run", "run.state", {"status": "succeeded"})
        assert len(attempts) == 2
    finally:
        bus.close()
