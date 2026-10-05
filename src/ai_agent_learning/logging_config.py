import json
import logging
import queue
from copy import copy
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ai_agent_learning.database import validate_database_url

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
    "node_name",
    "tool_call_id",
    "message_count",
    "decision_count",
    "queue_size",
    "approved",
)

_listener: QueueListener | None = None
_postgres_handler: "PostgresLogHandler | None" = None


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


class PostgresLogHandler(logging.Handler):
    """将结构化日志写入 PostgreSQL 数据库的 logs 表。"""

    def __init__(self, database_url: str) -> None:
        super().__init__()
        validate_database_url(database_url)
        self._connection = psycopg.connect(database_url, autocommit=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _record_payload(record)
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO logs (
                        timestamp, level, logger, event, session_id, trace_id,
                        message, model, tool_name, tool_call_id, duration_ms,
                        error_type, details_json, exception
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        datetime.fromtimestamp(record.created, tz=UTC),
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
                        Jsonb(payload, dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str)),
                        payload.get("exception"),
                    ),
                )
        except (OSError, psycopg.Error, TypeError, ValueError):
            # 日志失败不能中断 Agent，也不能向 Rich 动态显示区写错误文本。
            return

    def close(self) -> None:
        try:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
        finally:
            super().close()


class LocalQueueHandler(QueueHandler):
    """保留异常对象，供同一进程内的 PostgreSQL 写入线程格式化。"""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy(record)


def configure_logging(database_url: str, level: int = logging.INFO) -> None:
    """启动后台 PostgreSQL 日志写入器。"""
    global _listener, _postgres_handler

    shutdown_logging()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    postgres_handler = PostgresLogHandler(database_url)
    listener = QueueListener(log_queue, postgres_handler, respect_handler_level=True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(LocalQueueHandler(log_queue))

    _postgres_handler = postgres_handler
    _listener = listener
    listener.start()

    # 减少第三方 HTTP 库产生的普通请求日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """刷新队列并关闭 PostgreSQL 日志连接。"""
    global _listener, _postgres_handler

    if _listener is not None:
        _listener.stop()
        _listener = None
    if _postgres_handler is not None:
        _postgres_handler.close()
        _postgres_handler = None
    logging.getLogger().handlers.clear()
