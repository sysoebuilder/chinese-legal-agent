"""真实 PostgreSQL 验证；默认跳过，设置 CHECKPOINT_TEST_DATABASE_URL 后运行。

每个测试创建独立 schema，退出时删除，不修改应用现有会话。
"""

import asyncio
import logging
import os
from typing import TypedDict
from uuid import uuid4

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ai_agent_learning.database import migrate_database
from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer
from ai_agent_learning.logging_config import configure_logging, shutdown_logging


@pytest.fixture
def database_url():
    base_url = os.getenv("CHECKPOINT_TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("需要 CHECKPOINT_TEST_DATABASE_URL 指向测试 PostgreSQL")
    schema = "storage_test_" + uuid4().hex
    with psycopg.connect(base_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    url = make_conninfo(base_url, options=f"-c search_path={schema}")
    try:
        migrate_database(url)
        yield url
    finally:
        with psycopg.connect(base_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_migration_is_repeatable_and_creates_native_types(database_url):
    migrate_database(database_url)
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0001_sessions_logs",
        )
        columns = dict(connection.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'logs'"
        ).fetchall())
        assert columns["details_json"] == "jsonb"
        assert columns["timestamp"] == "timestamp with time zone"
        assert connection.execute("SELECT count(*) FROM sessions").fetchone() == (0,)


def test_queued_logs_flush_json_fields_and_exceptions(database_url):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        configure_logging(database_url)
        logger = logging.getLogger("storage.integration")
        logger.info("中文日志", extra={
            "event": "test.saved", "session_id": "test-session", "trace_id": "test-trace",
            "duration_ms": 12.5, "approved": False,
        })
        try:
            raise RuntimeError("测试异常")
        except RuntimeError:
            logger.exception("异常记录", extra={"event": "test.failed"})
        shutdown_logging()
        with psycopg.connect(database_url) as connection:
            rows = connection.execute(
                "SELECT message, session_id, trace_id, duration_ms, details_json, exception "
                "FROM logs WHERE logger = 'storage.integration' ORDER BY id"
            ).fetchall()
            assert len(rows) == 2
            assert rows[0][:4] == ("中文日志", "test-session", "test-trace", 12.5)
            assert rows[0][4]["approved"] is False
            assert "RuntimeError: 测试异常" in rows[1][5]
    finally:
        shutdown_logging()
        root.handlers[:] = original_handlers
        root.setLevel(original_level)


def test_thread_restores_checkpoint_and_pending_interrupt(database_url):
    class State(TypedDict):
        value: int

    def approve(state):
        interrupt("继续？")
        return {"value": state["value"] + 1}

    builder = StateGraph(State)
    builder.add_node("approve", approve)
    builder.add_edge(START, "approve")
    builder.add_edge("approve", END)

    async def scenario():
        config = {"configurable": {"thread_id": str(uuid4())}}
        async with open_checkpointer(database_url) as saver:
            graph = builder.compile(checkpointer=saver)
            await graph.ainvoke({"value": 1}, config)
            assert (await graph.aget_state(config)).next == ("approve",)
        async with open_checkpointer(database_url) as saver:
            graph = builder.compile(checkpointer=saver)
            assert (await graph.aget_state(config)).values == {"value": 1}
            assert (await graph.ainvoke(Command(resume=True), config))["value"] == 2
        process = await asyncio.create_subprocess_exec(
            os.sys.executable, "-c", "print('ok')", stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await process.communicate()
        assert output.strip() == b"ok"
    asyncio.run(scenario())
