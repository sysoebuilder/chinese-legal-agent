"""在 PostgreSQL 中管理会话目录，与 checkpoint 通过 thread_id 关联。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import LiteralString
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from ai_agent_learning.database import validate_database_url
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from ai_agent_learning.ui.terminal import TerminalUI

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 20
TITLE_PROMPT = """
根据用户开启本次对话时的第一条输入，生成一个简洁、具体的中文会话标题。
标题最多 20 个字符。只输出标题本身，不要引号、句号、前缀或解释。
你只负责生成标题，不需要回答用户的问题。
""".strip()

class Session(BaseModel):
    """会话目录记录；thread_id 同时用于定位 LangGraph 状态。"""

    thread_id: str
    name: str = ""
    created_at: str
    updated_at: str


class PostgresSessionStore:
    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    async def _execute(self, query: LiteralString, params: tuple = ()) -> None:
        def execute() -> None:
            with self._connection.cursor() as cursor:
                cursor.execute(query, params)
        await asyncio.to_thread(execute)

    async def list_sessions(self) -> list[Session]:
        def read() -> list[Session]:
            with self._connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT thread_id, name, created_at, updated_at "
                    "FROM sessions ORDER BY updated_at DESC"
                )
                return [
                    Session(
                        thread_id=row["thread_id"], name=row["name"],
                        created_at=row["created_at"].isoformat(),
                        updated_at=row["updated_at"].isoformat(),
                    )
                    for row in cursor.fetchall()
                ]
        return await asyncio.to_thread(read)

    async def create_session(self) -> Session:
        now = datetime.now(UTC)
        session = Session(
            thread_id=str(uuid4()), created_at=now.isoformat(), updated_at=now.isoformat(),
        )
        await self._execute(
            "INSERT INTO sessions (thread_id, name, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s)",
            (session.thread_id, session.name, now, now),
        )
        logger.info("PostgreSQL 会话创建完成", extra={
            "event": "session.created", "session_id": session.thread_id,
        })
        return session

    async def update_name(self, session: Session, name: str) -> None:
        normalized_name = name.strip()
        now = datetime.now(UTC)
        await self._execute(
            "UPDATE sessions SET name = %s, updated_at = %s WHERE thread_id = %s",
            (normalized_name, now, session.thread_id),
        )
        session.name = normalized_name
        session.updated_at = now.isoformat()
        logger.info("PostgreSQL 会话名称更新完成", extra={
            "event": "session.name.updated", "session_id": session.thread_id,
            "name_length": len(normalized_name),
        })

    async def touch(self, session: Session) -> None:
        now = datetime.now(UTC)
        await self._execute(
            "UPDATE sessions SET updated_at = %s WHERE thread_id = %s",
            (now, session.thread_id),
        )
        session.updated_at = now.isoformat()
        logger.info("PostgreSQL 会话更新时间已刷新", extra={
            "event": "session.touched", "session_id": session.thread_id,
        })


@asynccontextmanager
async def open_session_store(database_url: str):
    """独立连接在线程中执行数据库操作，兼容 Windows 默认事件循环。"""
    validate_database_url(database_url)
    connection = await asyncio.to_thread(psycopg.connect, database_url, autocommit=True)
    try:
        yield PostgresSessionStore(connection)
    finally:
        await asyncio.to_thread(connection.close)


async def create_session_name(
    model: BaseChatModel,
    first_user_input: str,
) -> str:
    """通过当前聊天模型非流式生成会话标题。"""
    response = await model.ainvoke(
        [
            SystemMessage(content=TITLE_PROMPT),
            HumanMessage(content=first_user_input),
        ],
        stream=False,
    )
    first_line = response.text.strip().splitlines()[0] if response.text.strip() else ""
    title = first_line.removeprefix("标题：").removeprefix("标题:").strip()
    title = title.strip("\"'“”‘’。")[:TITLE_MAX_LENGTH].strip()
    if not title:
        raise ValueError("模型没有生成有效的会话标题")
    return title


async def select_session(
    store: PostgresSessionStore,
    ui: TerminalUI,
) -> Session | None:
    """返回已有会话；选择新建时延迟到首条有效消息再创建。"""
    sessions = await store.list_sessions()
    ui.show_sessions(sessions)

    while True:
        choice = input("请选择会话编号: ").strip()
        if choice == "0":
            return None
        if not choice.isdigit():
            ui.show_error("请输入数字")
            continue

        index = int(choice) - 1
        if 0 <= index < len(sessions):
            session = sessions[index]
            logger.info(
                "PostgreSQL 会话选择完成",
                extra={
                    "event": "session.selected",
                    "session_id": session.thread_id,
                },
            )
            return session
        ui.show_error("会话编号不存在")
