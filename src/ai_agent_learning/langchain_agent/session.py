"""在 LangGraph 的 SQLite 数据库中管理会话目录。"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite
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

class SqliteSession(BaseModel):
    """会话目录记录；thread_id 同时用于定位 LangGraph 状态。"""

    thread_id: str
    name: str = ""
    created_at: str
    updated_at: str


class SqliteSessionStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                thread_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS sessions_updated_at
            ON sessions (updated_at DESC)
            """
        )
        await self._connection.commit()

    async def list_sessions(self) -> list[SqliteSession]:
        await self.setup()
        cursor = await self._connection.execute(
            """
            SELECT thread_id, name, created_at, updated_at
            FROM sessions
            ORDER BY updated_at DESC
            """
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [
            SqliteSession(
                thread_id=row[0],
                name=row[1],
                created_at=row[2],
                updated_at=row[3],
            )
            for row in rows
        ]

    async def create_session(self) -> SqliteSession:
        await self.setup()
        now = datetime.now(UTC).isoformat()
        session = SqliteSession(
            thread_id=str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        await self._connection.execute(
            """
            INSERT INTO sessions (thread_id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                session.thread_id,
                session.name,
                session.created_at,
                session.updated_at,
            ),
        )
        await self._connection.commit()
        logger.info(
            "SQLite 会话创建完成",
            extra={
                "event": "session.created",
                "session_id": session.thread_id,
            },
        )
        return session

    async def update_name(self, session: SqliteSession, name: str) -> None:
        normalized_name = name.strip()
        now = datetime.now(UTC).isoformat()
        await self._connection.execute(
            """
            UPDATE sessions
            SET name = ?, updated_at = ?
            WHERE thread_id = ?
            """,
            (normalized_name, now, session.thread_id),
        )
        await self._connection.commit()
        session.name = normalized_name
        session.updated_at = now
        logger.info(
            "SQLite 会话名称更新完成",
            extra={
                "event": "session.name.updated",
                "session_id": session.thread_id,
                "name_length": len(normalized_name),
            },
        )

    async def touch(self, session: SqliteSession) -> None:
        now = datetime.now(UTC).isoformat()
        await self._connection.execute(
            """
            UPDATE sessions
            SET updated_at = ?
            WHERE thread_id = ?
            """,
            (now, session.thread_id),
        )
        await self._connection.commit()
        session.updated_at = now
        logger.info(
            "SQLite 会话更新时间已刷新",
            extra={
                "event": "session.touched",
                "session_id": session.thread_id,
            },
        )


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


async def select_sqlite_session(
    store: SqliteSessionStore,
    ui: TerminalUI,
) -> SqliteSession:
    """显示 SQLite 会话目录并返回用户选择或新建的会话。"""
    sessions = await store.list_sessions()
    ui.show_sessions(sessions)

    while True:
        choice = input("请选择会话编号: ").strip()
        if choice == "0":
            return await store.create_session()
        if not choice.isdigit():
            ui.show_error("请输入数字")
            continue

        index = int(choice) - 1
        if 0 <= index < len(sessions):
            session = sessions[index]
            logger.info(
                "SQLite 会话选择完成",
                extra={
                    "event": "session.selected",
                    "session_id": session.thread_id,
                },
            )
            return session
        ui.show_error("会话编号不存在")
