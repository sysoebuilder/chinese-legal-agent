"""新版 Agent 的基础工具执行日志中间件。"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

logger = logging.getLogger(__name__)


class ToolLoggingMiddleware(AgentMiddleware):
    """记录工具执行状态和耗时，不保存参数或返回正文。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest], Awaitable[ToolMessage | Command[Any]]
        ],
    ) -> ToolMessage | Command[Any]:
        tool_name = (
            request.tool.name if request.tool is not None else request.tool_call["name"]
        )
        tool_call_id = request.tool_call.get("id")
        config = get_config()
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        fields = {
            "session_id": configurable.get("thread_id"),
            "trace_id": metadata.get("trace_id"),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
        }
        started_at = time.perf_counter()
        logger.info(
            "工具执行开始",
            extra={"event": "tool.call.started", **fields},
        )
        try:
            result = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            logger.exception(
                "工具执行失败",
                extra={
                    "event": "tool.call.failed",
                    "duration_ms": round(
                        (time.perf_counter() - started_at) * 1000,
                        2,
                    ),
                    "error_type": type(error).__name__,
                    **fields,
                },
            )
            raise

        logger.info(
            "工具执行完成",
            extra={
                "event": "tool.call.completed",
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
                **fields,
            },
        )
        return result
