import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from ai_agent_learning.paths import get_data_dir

LOG_DIR = get_data_dir() / "logs"
LOG_FILE = LOG_DIR / "app.jsonl"


class JsonFormatter(logging.Formatter):
    """将 LogRecord 转换成一行 JSON。"""

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

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field_name in self.EXTRA_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        )


def configure_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter()

    file_handler = RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    # 减少第三方 HTTP 库产生的普通请求日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
