"""显式启用的 PostgreSQL 集成测试；只操作随机创建的隔离 schema。"""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ai_agent_learning.api.celery_app import dispatch_pending
from ai_agent_learning.api.store import PostgresRunStore, RunConflict
from ai_agent_learning.database import get_database_url, migrate_database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_TEST_POSTGRES") != "1", reason="默认测试离线"
)
MODEL = {"provider": "glm", "model": "offline-test", "base_url": "https://example.org"}


@pytest.fixture
def store():
    database_url = get_database_url()
    schema = f"api_test_{uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated_url = make_conninfo(database_url, options=f"-c search_path={schema}")
    try:
        migrate_database(isolated_url)
        migrate_database(isolated_url)
        yield PostgresRunStore(isolated_url)
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_persisted_outbox_reopen_and_state_without_event_table(store):
    run = store.create("测试", None, MODEL)
    run_id = run["run_id"]
    reopened = PostgresRunStore(store.database_url)
    assert reopened.get(run_id)["status"] == "queued"
    with pytest.raises(RunConflict):
        reopened.create("重复会话", run["thread_id"], MODEL)

    class Publisher:
        def __init__(self):
            self.published = []

        def publish(self, run_id, generation):
            self.published.append((run_id, generation))

    publisher = Publisher()
    assert dispatch_pending(reopened, publisher) == 1
    assert publisher.published == [(run_id, 1)]
    assert not store.get(run_id)["dispatch_pending"]
    with store.thread_lock(run["thread_id"]) as acquired:
        assert acquired
        with reopened.thread_lock(run["thread_id"]) as duplicate:
            assert not duplicate
        store.begin(run_id, 1)
        store.finish(run_id, 1, "succeeded", {"answer": "完成"})
    assert reopened.get(run_id)["revision"] == 3
    with store._connect() as connection:
        assert (
            connection.execute("SELECT to_regclass('agent_run_events')").fetchone()[
                "to_regclass"
            ]
            is None
        )
    assert reopened.view(reopened.get(run_id)).answer == "完成"
    assert (
        store.create("下一轮", run["thread_id"], MODEL)["thread_id"] == run["thread_id"]
    )


def test_shared_history_paginates_old_checkpoints_after_compaction(store):
    from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    thread_id = str(uuid4())
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO sessions (thread_id,name,created_at,updated_at) "
            "VALUES (%s,'terminal conversation',now(),now())",
            (thread_id,),
        )
    assert store.history(thread_id, 10, None).messages == []

    def answer(state):
        user = state["messages"][-1]
        return {
            "messages": [AIMessage(id=f"reply-{user.id}", content=f"reply:{user.text}")]
        }

    config = {"configurable": {"thread_id": thread_id}}
    with PostgresSaver.from_conn_string(store.database_url) as saver:
        saver.setup()
        graph = StateGraph(MessagesState)
        graph.add_node("answer", answer)
        graph.add_edge(START, "answer")
        graph.add_edge("answer", END)
        agent = graph.compile(checkpointer=saver)
        for index in range(12):
            agent.invoke(
                {
                    "messages": [
                        HumanMessage(
                            id=f"terminal-user-{index}",
                            content=f"question-{index}",
                        )
                    ]
                },
                config,
            )
        state = agent.get_state(config)
        agent.update_state(
            config,
            {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    HumanMessage(
                        id="summary",
                        content="internal summary",
                        additional_kwargs={"lc_source": "summarization"},
                    ),
                    *state.values["messages"][-6:],
                ]
            },
            as_node="answer",
        )
    page = store.history(thread_id, 10, None)
    assert len(page.messages) == 20
    assert page.messages[0].id == "terminal-user-2"
    assert page.next_before == "terminal-user-2"
    older = store.history(thread_id, 10, page.next_before)
    assert len(older.messages) == 4 and not older.has_more
    assert store.sessions(1, 0).sessions[0].thread_id == UUID(thread_id)
    with pytest.raises(ValueError):
        store.history(thread_id, 10, "other-session-id")


def test_shared_history_reads_api_and_terminal_turns_together(store):
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, MessagesState, StateGraph

    from ai_agent_learning.api.worker import execute_run

    graph = StateGraph(MessagesState)
    graph.add_node(
        "answer",
        lambda state: {
            "messages": [
                AIMessage(
                    content=f"reply:{state['messages'][-1].text}",
                )
            ]
        },
    )
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)

    @asynccontextmanager
    async def factory(url, model):
        from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer

        async with open_checkpointer(url) as saver:
            yield graph.compile(checkpointer=saver)

    run = store.create("API question", None, MODEL)
    execute_run(store, run["run_id"], 1, factory)
    with PostgresSaver.from_conn_string(store.database_url) as saver:
        agent = graph.compile(checkpointer=saver)
        agent.invoke(
            {"messages": [HumanMessage(content="terminal question")]},
            {"configurable": {"thread_id": run["thread_id"]}},
        )
    page = store.history(run["thread_id"], 10, None)
    assert [message.content for message in page.messages] == [
        "API question",
        "reply:API question",
        "terminal question",
        "reply:terminal question",
    ]


def test_cancel_finish_race_and_resume_generation_are_atomic(store):
    row = store.create("审批", None, MODEL)
    run_id = row["run_id"]
    store.begin(run_id, 1)
    pending = [
        {"id": "i1", "value": {"action_requests": [{"name": "send_email", "args": {}}]}}
    ]
    store.interrupted(run_id, 1, pending)
    assert store.get(run_id)["status"] == "waiting_approval"
    with pytest.raises(ValueError):
        store.resume(run_id, {"stale": {}})
    resumed = store.resume(run_id, {"i1": {"decisions": [{"type": "reject"}]}})
    assert resumed["generation"] == 2
    store.dispatched(run_id, 1)
    assert store.get(run_id)["dispatch_pending"]
    assert store.begin(run_id, 1) is None
    store.begin(run_id, 2)
    store.cancel(run_id)
    store.finish(run_id, 2, "succeeded", {"answer": "过期回答"})
    final = store.get(run_id)
    assert final["status"] == "cancelled" and final["answer"] is None
    assert final["revision"] >= 6


def test_real_postgres_checkpoint_survives_worker_restart_and_approval(store):
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import interrupt

    from ai_agent_learning.api.worker import execute_run
    from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer

    effects = []

    def approval(state):
        response = interrupt({"action_requests": [{"name": "send_email", "args": {}}]})
        effects.append(response)
        return {"messages": [AIMessage(content="审批完成")]}

    @asynccontextmanager
    async def factory(url, config):
        async with open_checkpointer(url) as saver:
            graph = StateGraph(MessagesState)
            graph.add_node("approval", approval)
            graph.add_edge(START, "approval")
            graph.add_edge("approval", END)
            yield graph.compile(checkpointer=saver)

    row = store.create("开始", None, MODEL)
    run_id = row["run_id"]
    execute_run(store, run_id, 1, factory)
    pending = store.get(run_id)["interrupts"]
    assert effects == []
    store.resume(run_id, {pending[0]["id"]: {"decisions": [{"type": "approve"}]}})
    # 新建 saver/graph 模拟 worker 进程重启。
    execute_run(PostgresRunStore(store.database_url), run_id, 2, factory)
    assert len(effects) == 1
    assert store.get(run_id)["answer"] == "审批完成"

    async def inspect():
        async with factory(store.database_url, MODEL) as agent:
            return await agent.aget_state(
                {"configurable": {"thread_id": row["thread_id"]}}
            )

    snapshot = asyncio.run(inspect())
    assert (
        len([m for m in snapshot.values["messages"] if isinstance(m, HumanMessage)])
        == 1
    )


def recovery_graph(calls, *, approval=False):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import interrupt

    def first(state):
        calls.append("first")
        return {}

    def second(state):
        if approval:
            calls.append(
                interrupt({"action_requests": [{"name": "send_email", "args": {}}]})
            )
        else:
            calls.append("second")
        return {"messages": [AIMessage(content="断点恢复成功")]}

    graph = StateGraph(MessagesState)
    # 与生产顶层图相同的节点名，使用生产状态读取器检查真实 checkpoint。
    graph.add_node("legal_flow", first)
    graph.add_node("general_agent", second)
    graph.add_edge(START, "legal_flow")
    graph.add_edge("legal_flow", "general_agent")
    graph.add_edge("general_agent", END)
    return graph


def write_checkpoint(store, row, graph, *, paused=True):
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(store.database_url) as saver:
        saver.setup()
        agent = graph.compile(
            checkpointer=saver, interrupt_after=["legal_flow"] if paused else None
        )
        agent.invoke(
            {
                "messages": [
                    HumanMessage(id=f"run:{row['run_id']}:user", content=row["message"])
                ]
            },
            {
                "configurable": {"thread_id": row["thread_id"]},
                "metadata": {"run_id": row["run_id"]},
            },
        )


def checkpoint_factory(graph):
    from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer

    @asynccontextmanager
    async def factory(url, model):
        async with open_checkpointer(url) as saver:
            yield graph.compile(checkpointer=saver)

    return factory


def test_running_checkpoint_reconnect_is_fenced_and_does_not_repeat_finished_node(
    store,
):
    from langchain_core.messages import HumanMessage

    from ai_agent_learning.api.worker import execute_run

    calls = []
    graph = recovery_graph(calls)
    row = store.create("开始", None, MODEL)
    store.begin(row["run_id"], 1)
    write_checkpoint(store, row, graph)
    # 数据库执行连接随进程退出释放；run 仍为 running。
    reopened = PostgresRunStore(store.database_url)
    recovery = reopened.recovery(row["thread_id"])
    assert recovery.can_reconnect and recovery.pending_nodes == ["general_agent"]
    with store.thread_lock(row["thread_id"]) as acquired:
        assert acquired
        busy = reopened.recovery(row["thread_id"])
        assert busy.busy and not busy.can_reconnect
        with pytest.raises(RunConflict, match="Worker"):
            reopened.reconnect(row["run_id"])
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from ai_agent_learning.api.app import create_app
    from ai_agent_learning.api.settings import ApiSettings

    published = []
    app = create_app(
        store=reopened,
        publisher=SimpleNamespace(publish=lambda *entry: published.append(entry)),
        settings=ApiSettings(token="test-token", dispatch_interval=0.01),
        model_loader=lambda: pytest.fail("检查和恢复断点不应加载模型密钥"),
    )
    recovery_path = f"/v1/sessions/{row['thread_id']}/recovery"
    reconnect_path = f"/v1/runs/{row['run_id']}/reconnect"
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get(recovery_path).status_code == 401
        assert client.post(reconnect_path).status_code == 401
        assert client.get(recovery_path, headers=headers).json()["can_reconnect"]
        assert (
            client.get(f"/v1/sessions/{uuid4()}/recovery", headers=headers).status_code
            == 404
        )
        response = client.post(reconnect_path, headers=headers)
        assert response.status_code == 202 and response.json()["status"] == "queued"
        assert client.post(reconnect_path, headers=headers).status_code == 409
    queued = reopened.get(row["run_id"])
    assert queued["generation"] == 2 and queued["status"] == "queued"
    assert queued["dispatch_pending"] or (row["run_id"], 2) in published
    with pytest.raises(RunConflict):
        reopened.reconnect(row["run_id"])
    execute_run(reopened, row["run_id"], 1, checkpoint_factory(graph))
    assert calls == ["first"]
    execute_run(reopened, row["run_id"], 2, checkpoint_factory(graph))
    assert calls == ["first", "second"]
    assert reopened.get(row["run_id"])["answer"] == "断点恢复成功"
    assert reopened.recovery(row["thread_id"]).run is None

    async def inspect():
        async with checkpoint_factory(graph)(store.database_url, MODEL) as agent:
            return await agent.aget_state(
                {"configurable": {"thread_id": row["thread_id"]}}
            )

    snapshot = asyncio.run(inspect())
    assert sum(isinstance(m, HumanMessage) for m in snapshot.values["messages"]) == 1


def test_reconnect_restores_checkpoint_interrupt_without_approving_it(store):
    from ai_agent_learning.api.worker import execute_run

    calls = []
    graph = recovery_graph(calls, approval=True)
    row = store.create("审批", None, MODEL)
    store.begin(row["run_id"], 1)
    # 模拟 checkpoint 已保存中断，但 worker 尚未写入 waiting_approval 就退出。
    write_checkpoint(store, row, graph, paused=False)
    assert store.get(row["run_id"])["interrupts"] == []
    assert store.recovery(row["thread_id"]).can_reconnect
    store.reconnect(row["run_id"])
    execute_run(store, row["run_id"], 2, checkpoint_factory(graph))
    waiting = store.get(row["run_id"])
    assert waiting["status"] == "waiting_approval" and calls == ["first"]
    decision = {"decisions": [{"type": "reject"}]}
    store.resume(row["run_id"], {waiting["interrupts"][0]["id"]: decision})
    execute_run(store, row["run_id"], 3, checkpoint_factory(graph))
    assert calls == ["first", decision]


def test_reconnect_rejects_completed_missing_cancelled_or_other_run_checkpoint(store):
    graph = recovery_graph([])
    row = store.create("无断点", None, MODEL)
    store.begin(row["run_id"], 1)
    assert not store.recovery(row["thread_id"]).can_reconnect
    with pytest.raises(RunConflict, match="checkpoint"):
        store.reconnect(row["run_id"])
    write_checkpoint(store, row, graph, paused=False)
    assert not store.recovery(row["thread_id"]).can_reconnect
    with pytest.raises(RunConflict):
        store.reconnect(row["run_id"])
    store.finish(row["run_id"], 1, "succeeded", {"answer": "完成"})
    # 上一轮的 checkpoint 不能恢复到新 run。
    next_row = store.create("下一轮", row["thread_id"], MODEL)
    store.begin(next_row["run_id"], 1)
    write_checkpoint(store, row, graph)
    assert not store.recovery(row["thread_id"]).can_reconnect
    with pytest.raises(RunConflict):
        store.reconnect(next_row["run_id"])
    store.cancel(next_row["run_id"])
    with pytest.raises(RunConflict):
        store.reconnect(next_row["run_id"])


def test_upgrade_removes_old_delta_events_but_preserves_run_and_checkpoint(store):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    graph = recovery_graph([])
    row = store.create("保留断点", None, MODEL)
    store.begin(row["run_id"], 1)
    write_checkpoint(store, row, graph)
    config = Config()
    config.set_main_option("script_location", "ai_agent_learning:migrations")
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(store.database_url),
        poolclass=NullPool,
    )
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0002_api_runs")
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO agent_run_events (run_id,id,event,data) VALUES (%s,1,'reasoning.delta','{\"text\": \"old\"}')",
                (row["run_id"],),
            )
        migrate_database(store.database_url)
        assert store.get(row["run_id"])["thread_id"] == row["thread_id"]
        assert store.recovery(row["thread_id"]).can_reconnect
        with store._connect() as connection:
            assert (
                connection.execute("SELECT to_regclass('agent_run_events')").fetchone()[
                    "to_regclass"
                ]
                is None
            )
    finally:
        engine.dispose()
