"""PostgreSQL 保存任务状态；实时事件在事务提交后通过 Redis 推送。"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from ai_agent_learning.api.history import checkpoint_messages, history_page
from ai_agent_learning.api.schemas import (
    HistoryPage,
    RecoveryView,
    RunView,
    SessionPage,
    SessionView,
    validate_responses,
)


class RunNotFound(Exception):
    pass


class RunConflict(Exception):
    pass


class RunStore(Protocol):
    """HTTP 接口和发布器所需的存储能力，也供离线适配器实现。"""

    def get(self, run_id: str) -> dict[str, Any]: ...
    def create(
        self, message: str, thread_id: str | None, model_config: dict[str, str]
    ) -> dict[str, Any]: ...
    def resume(
        self, run_id: str, responses: dict[str, dict[str, Any]]
    ) -> dict[str, Any]: ...
    def cancel(self, run_id: str) -> dict[str, Any]: ...
    def pending_dispatches(self) -> list[dict[str, Any]]: ...
    def dispatched(self, run_id: str, generation: int) -> None: ...
    def sessions(self, limit: int, offset: int) -> SessionPage: ...
    def delete_session(self, thread_id: str) -> None: ...
    def history(
        self, thread_id: str, limit: int, before: str | None
    ) -> HistoryPage: ...
    def recovery(self, thread_id: str) -> RecoveryView: ...
    def reconnect(self, run_id: str) -> dict[str, Any]: ...


class PostgresRunStore:
    def __init__(self, database_url: str, event_bus=None) -> None:
        self.database_url = database_url
        self.event_bus = event_bus

    @contextmanager
    def _transaction(self, run_id: str):
        with self._connect() as connection, connection.cursor() as cursor:
            yield cursor
            row = self._read(cursor, run_id)
        # 数据库提交成功后才通知；Redis 失败不回滚已保存的任务状态。
        if self.event_bus is not None:
            self.event_bus.publish(
                run_id, "run.state", self.view(row).model_dump(mode="json")
            )

    def _connect(self, *, autocommit: bool = False) -> psycopg.Connection[DictRow]:
        return psycopg.Connection[DictRow].connect(
            self.database_url, row_factory=dict_row, autocommit=autocommit
        )

    @staticmethod
    def view(row: dict[str, Any]) -> RunView:
        config = row["model_config"]
        return RunView.model_validate(
            {**row, "provider": config["provider"], "model": config["model"]}
        )

    @staticmethod
    def _changed(cursor: psycopg.Cursor[DictRow], run_id: str) -> None:
        cursor.execute(
            "UPDATE agent_runs SET revision = revision + 1, updated_at = now() "
            "WHERE run_id = %s RETURNING revision",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RunNotFound(run_id)

    @staticmethod
    def _read(
        cursor: psycopg.Cursor[DictRow], run_id: str, *, lock: bool = False
    ) -> dict[str, Any]:
        query = "SELECT * FROM agent_runs WHERE run_id = %s"
        if lock:
            query += " FOR UPDATE"
        cursor.execute(query, (run_id,))
        row = cursor.fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return row

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            return self._read(cursor, run_id)

    def _checkpoint_state(self, row: dict[str, Any]):
        from ai_agent_learning.langchain_agent.runtime import create_new_chat_agent
        from ai_agent_learning.model_config import ModelConfig

        public = row["model_config"]
        # 只构建图并读状态，不执行节点、连接 MCP 或请求模型，无需读取密钥。
        config = ModelConfig(
            public["provider"], public["model"], "checkpoint-reader", public["base_url"]
        )
        with self._connect() as connection:
            table = connection.execute(
                "SELECT to_regclass('checkpoints') AS name"
            ).fetchone()
            if table is None or table["name"] is None:
                return None
            agent = create_new_chat_agent(
                config, checkpointer=PostgresSaver(connection)
            )
            return agent.get_state(
                RunnableConfig(configurable={"thread_id": row["thread_id"]})
            )

    def _pending_nodes(self, row: dict[str, Any]) -> list[str]:
        from ai_agent_learning.api.execution import checkpoint_started
        from ai_agent_learning.langchain_agent.checkpoint import has_pending_turn

        snapshot = self._checkpoint_state(row)
        if (
            snapshot is None
            or not checkpoint_started(snapshot, row)
            or not has_pending_turn(snapshot)
        ):
            return []
        return list(snapshot.next)

    def recovery(self, thread_id: str) -> RecoveryView:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM sessions WHERE thread_id = %s", (thread_id,))
            if cursor.fetchone() is None:
                raise RunNotFound(thread_id)
            cursor.execute(
                "SELECT * FROM agent_runs WHERE thread_id = %s AND status NOT IN ('succeeded','cancelled') "
                "ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return RecoveryView()
        if row["status"] not in {"running", "failed"} or row["cancel_requested"]:
            return RecoveryView(run=self.view(row))
        with self.thread_lock(thread_id) as acquired:
            # Worker 持锁时绝不能把慢模型请求误判成掉线。
            row = self.get(row["run_id"])
            pending = self._pending_nodes(row) if acquired else []
            return RecoveryView(
                run=self.view(row),
                busy=not acquired,
                pending_nodes=pending,
                can_reconnect=bool(pending)
                and row["status"] in {"running", "failed"}
                and not row["cancel_requested"],
            )

    def reconnect(self, run_id: str) -> dict[str, Any]:
        initial = self.get(run_id)
        with self.thread_lock(initial["thread_id"]) as acquired:
            if not acquired:
                raise RunConflict("Worker 仍在执行该会话，请等待")
            with self._transaction(run_id) as cursor:
                row = self._read(cursor, run_id, lock=True)
                if (
                    row["status"] not in {"running", "failed"}
                    or row["cancel_requested"]
                ):
                    raise RunConflict("该运行当前不可重连")
                if not self._pending_nodes(row):
                    raise RunConflict("当前运行没有可恢复的 checkpoint 断点")
                # 保留已经提交的审批回答。新 generation 使队列中的旧消息失效。
                cursor.execute(
                    "UPDATE agent_runs SET status = 'queued', generation = generation + 1, "
                    "dispatch_pending = true, error = NULL WHERE run_id = %s",
                    (run_id,),
                )
                self._changed(cursor, run_id)
                return self._read(cursor, run_id)

    def sessions(self, limit: int, offset: int) -> SessionPage:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT thread_id,name,created_at,updated_at FROM sessions "
                "ORDER BY updated_at DESC,thread_id DESC LIMIT %s OFFSET %s",
                (limit + 1, offset),
            )
            rows = cursor.fetchall()
        has_more = len(rows) > limit
        return SessionPage(
            sessions=[SessionView.model_validate(row) for row in rows[:limit]],
            has_more=has_more,
            next_offset=offset + limit if has_more else None,
        )

    def delete_session(self, thread_id: str) -> None:
        """与 worker 互斥，在同一事务中删除运行、全部图断点和会话目录。"""
        with self.thread_lock(thread_id) as acquired:
            if not acquired:
                raise RunConflict("该会话仍在执行，请先停止并等待任务结束后再删除")
            try:
                with self._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT thread_id FROM sessions WHERE thread_id = %s FOR UPDATE NOWAIT",
                        (thread_id,),
                    )
                    if cursor.fetchone() is None:
                        raise RunNotFound(thread_id)
                    # 取消/恢复接口先锁 run；不等待这些行锁，避免反向等待会话锁。
                    cursor.execute(
                        "SELECT run_id FROM agent_runs WHERE thread_id = %s FOR UPDATE NOWAIT",
                        (thread_id,),
                    )
                    cursor.fetchall()
                    # 兼容刚创建、worker 尚未初始化 checkpoint 表的会话。
                    cursor.execute("SELECT to_regclass('checkpoints') AS table_name")
                    table = cursor.fetchone()
                    if table is not None and table["table_name"] is not None:
                        # 官方删除方法覆盖所有 namespace 的 checkpoints/blobs/writes。
                        PostgresSaver(connection).delete_thread(thread_id)
                    cursor.execute(
                        "DELETE FROM agent_runs WHERE thread_id = %s", (thread_id,)
                    )
                    cursor.execute(
                        "DELETE FROM sessions WHERE thread_id = %s", (thread_id,)
                    )
            except psycopg.errors.LockNotAvailable:
                raise RunConflict("该会话状态正在更新，请稍后重试删除") from None

    def history(self, thread_id: str, limit: int, before: str | None) -> HistoryPage:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM sessions WHERE thread_id = %s", (thread_id,))
            if cursor.fetchone() is None:
                raise RunNotFound(thread_id)
            # 第一次任务执行前，worker 可能还没有创建 checkpoint 表。
            cursor.execute("SELECT to_regclass('checkpoints') AS table_name")
            row = cursor.fetchone()
            if row is None or row["table_name"] is None:
                return history_page(thread_id, [], limit, before)
            saver = PostgresSaver(connection)
            return history_page(
                thread_id,
                checkpoint_messages(saver, thread_id),
                limit,
                before,
            )

    @staticmethod
    def _ensure_session(
        cursor: psycopg.Cursor[DictRow], thread_id: str | None, message: str
    ) -> str:
        if thread_id is not None:
            cursor.execute(
                "SELECT thread_id FROM sessions WHERE thread_id = %s FOR KEY SHARE",
                (thread_id,),
            )
            if cursor.fetchone() is None:
                raise RunNotFound(thread_id)
            return thread_id

        thread_id = str(uuid4())
        cursor.execute(
            "INSERT INTO sessions (thread_id,name,created_at,updated_at) "
            "VALUES (%s,%s,now(),now())",
            (thread_id, message[:20]),
        )
        return thread_id

    def create(
        self, message: str, thread_id: str | None, model_config: dict[str, str]
    ) -> dict[str, Any]:
        run_id = str(uuid4())
        try:
            with self._transaction(run_id) as cursor:
                thread_id = self._ensure_session(cursor, thread_id, message)
                cursor.execute(
                    "INSERT INTO agent_runs (run_id,thread_id,message,model_config,status) "
                    "VALUES (%s,%s,%s,%s,'queued')",
                    (run_id, thread_id, message, Jsonb(model_config)),
                )
                self._changed(cursor, run_id)
                return self._read(cursor, run_id)
        except psycopg.errors.UniqueViolation:
            raise RunConflict("该会话仍有未解决的运行，请先恢复或取消") from None

    def pending_dispatches(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT run_id,generation FROM agent_runs WHERE dispatch_pending "
                "AND status = 'queued' AND NOT cancel_requested ORDER BY created_at LIMIT 100",
            )
            return list(cursor.fetchall())

    def dispatched(self, run_id: str, generation: int) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_runs SET dispatch_pending = false WHERE run_id = %s AND generation = %s",
                (run_id, generation),
            )

    @contextmanager
    def thread_lock(self, thread_id: str) -> Iterator[bool]:
        # 独立数据库连接持有锁；worker 崩溃时 PostgreSQL 自动释放。
        with (
            self._connect(autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 71)) AS acquired",
                (thread_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise psycopg.DatabaseError("未取得会话执行锁的查询结果")
            acquired = bool(row["acquired"])
            try:
                yield acquired
            finally:
                if acquired:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 71))",
                        (thread_id,),
                    )

    def begin(self, run_id: str, generation: int) -> dict[str, Any] | None:
        with self._transaction(run_id) as cursor:
            row = self._read(cursor, run_id, lock=True)
            if row["generation"] != generation or row["status"] not in {
                "queued",
                "running",
            }:
                return None
            if row["cancel_requested"]:
                self._complete(cursor, run_id, "cancelled", {})
                return None
            cursor.execute(
                "UPDATE agent_runs SET status = 'running', dispatch_pending = false WHERE run_id = %s",
                (run_id,),
            )
            self._changed(cursor, run_id)
            return self._read(cursor, run_id)

    def interrupted(
        self, run_id: str, generation: int, interrupts: list[dict[str, Any]]
    ) -> None:
        with self._transaction(run_id) as cursor:
            row = self._read(cursor, run_id, lock=True)
            if row["generation"] != generation or row["status"] != "running":
                return
            if row["cancel_requested"]:
                self._complete(cursor, run_id, "cancelled", {})
                return
            status = (
                "waiting_approval"
                if any("action_requests" in i["value"] for i in interrupts)
                else "waiting_input"
            )
            cursor.execute(
                "UPDATE agent_runs SET status = %s, interrupts = %s, resume_values = '{}'::jsonb WHERE run_id = %s",
                (status, Jsonb(interrupts), run_id),
            )
            self._changed(cursor, run_id)

    def resume(
        self, run_id: str, responses: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        with self._transaction(run_id) as cursor:
            row = self._read(cursor, run_id, lock=True)
            if (
                row["status"] not in {"waiting_approval", "waiting_input", "failed"}
                or row["cancel_requested"]
            ):
                raise RunConflict("该运行当前不可恢复")
            validate_responses(row["interrupts"], responses)
            cursor.execute(
                "UPDATE agent_runs SET status = 'queued', generation = generation + 1, "
                "dispatch_pending = true, resume_values = %s, error = NULL WHERE run_id = %s",
                (Jsonb(responses), run_id),
            )
            self._changed(cursor, run_id)
            return self._read(cursor, run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._transaction(run_id) as cursor:
            row = self._read(cursor, run_id, lock=True)
            if row["status"] in {"succeeded", "cancelled"}:
                return row
            if not row["cancel_requested"]:
                cursor.execute(
                    "UPDATE agent_runs SET cancel_requested = true WHERE run_id = %s",
                    (run_id,),
                )
                self._changed(cursor, run_id)
            if row["status"] != "running":
                self._complete(cursor, run_id, "cancelled", {})
            return self._read(cursor, run_id)

    def _complete(
        self,
        cursor: psycopg.Cursor[DictRow],
        run_id: str,
        status: str,
        data: dict[str, Any],
    ) -> None:
        cursor.execute(
            "UPDATE agent_runs SET status = %s, answer = %s, error = %s, "
            "interrupts = '[]'::jsonb, resume_values = '{}'::jsonb, dispatch_pending = false WHERE run_id = %s",
            (status, data.get("answer"), data.get("error"), run_id),
        )
        cursor.execute(
            "UPDATE sessions SET updated_at = now() WHERE thread_id = "
            "(SELECT thread_id FROM agent_runs WHERE run_id = %s)",
            (run_id,),
        )
        self._changed(cursor, run_id)

    def finish(
        self, run_id: str, generation: int, status: str, data: dict[str, Any]
    ) -> None:
        with self._transaction(run_id) as cursor:
            row = self._read(cursor, run_id, lock=True)
            if row["generation"] != generation or row["status"] != "running":
                return
            if row["cancel_requested"]:
                status, data = "cancelled", {}
            self._complete(cursor, run_id, status, data)
