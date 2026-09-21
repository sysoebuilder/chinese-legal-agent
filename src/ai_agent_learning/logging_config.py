import json
import logging
import queue
import sqlite3
from copy import copy
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Any


EXTRA_FIELDS = (
    "event",
    "session_id",
    "trace_id",
    "mode",
    "model",
    "input_tokens",
    "input_tokens_before",
    "input_tokens_after",
    "context_limit",
    "duration_ms",
    "compacted_messages",
    "retained_messages",
    "reason",
    "file_path",
    "session_dir",
    "session_count",
    "file_count",
    "error_type",
    "name_length",
    "tool_name",
    "tool_call_id",
    "message_count",
    "decision_count",
    "queue_size",
    "approved",
)

_listener: QueueListener | None = None
_sqlite_handler: "SQLiteLogHandler | None" = None


def _record_payload(record: logging.LogRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for field_name in EXTRA_FIELDS:
        value = getattr(record, field_name, None)
        if value is not None:
            payload[field_name] = value
    if record.exc_info:
        payload["exception"] = logging.Formatter().formatException(record.exc_info)
    return payload


class SQLiteLogHandler(logging.Handler):
    """将结构化日志写入 LangGraph checkpoint 数据库的 logs 表。"""

    def __init__(self, database_path: Path) -> None:
        super().__init__()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            database_path,
            timeout=5,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                logger TEXT NOT NULL,
                event TEXT,
                session_id TEXT,
                trace_id TEXT,
                message TEXT NOT NULL,
                model TEXT,
                tool_name TEXT,
                tool_call_id TEXT,
                duration_ms REAL,
                error_type TEXT,
                details_json TEXT NOT NULL,
                exception TEXT
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS logs_timestamp ON logs (timestamp DESC)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS logs_event ON logs (event)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS logs_session_id ON logs (session_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS logs_trace_id ON logs (trace_id)"
        )
        self._connection.commit()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _record_payload(record)
            self._connection.execute(
                """
                INSERT INTO logs (
                    timestamp, level, logger, event, session_id, trace_id,
                    message, model, tool_name, tool_call_id, duration_ms,
                    error_type, details_json, exception
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["timestamp"],
                    payload["level"],
                    payload["logger"],
                    payload.get("event"),
                    payload.get("session_id"),
                    payload.get("trace_id"),
                    payload["message"],
                    payload.get("model"),
                    payload.get("tool_name"),
                    payload.get("tool_call_id"),
                    payload.get("duration_ms"),
                    payload.get("error_type"),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    payload.get("exception"),
                ),
            )
            self._connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            # 日志失败不能中断 Agent，也不能向 Rich 动态显示区写错误文本。
            return

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            super().close()


class LocalQueueHandler(QueueHandler):
    """保留异常对象，供同一进程内的 SQLite 写入线程格式化。"""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy(record)


def configure_logging(database_path: Path, level: int = logging.INFO) -> None:
    """启动后台 SQLite 日志写入器。"""
    global _listener, _sqlite_handler

    shutdown_logging()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    sqlite_handler = SQLiteLogHandler(database_path)
    listener = QueueListener(log_queue, sqlite_handler, respect_handler_level=True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(LocalQueueHandler(log_queue))

    _sqlite_handler = sqlite_handler
    _listener = listener
    listener.start()

    # 减少第三方 HTTP 库产生的普通请求日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """刷新队列并关闭 SQLite 日志连接。"""
    global _listener, _sqlite_handler

    if _listener is not None:
        _listener.stop()
        _listener = None
    if _sqlite_handler is not None:
        _sqlite_handler.close()
        _sqlite_handler = None
    logging.getLogger().handlers.clear()
