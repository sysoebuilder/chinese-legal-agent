"""异常组不能遮盖实际错误，也不能把上游凭据发送给客户端。"""

import asyncio
from contextlib import asynccontextmanager

from ai_agent_learning.api.errors import (
    WindowsTerminalUnavailable,
    exception_diagnostics,
    public_error,
)
from ai_agent_learning.api.worker import run_agent


def test_nested_groups_report_leaf_types_without_upstream_text():
    error = ExceptionGroup("private-group-text", [
        ExceptionGroup("private-inner-text", [ValueError("private-api-key")]),
        ConnectionError("private-connection-url"),
    ])
    assert public_error(error) == "ValueError, ConnectionError"
    diagnostics = str(exception_diagnostics(error))
    assert "private" not in diagnostics
    assert "ConnectionError" in diagnostics


def test_worker_reports_terminal_connection_error_through_task_group():
    updates = []

    class Store:
        database_url = "unused"

        def finish(self, *args):
            updates.append(args)

    @asynccontextmanager
    async def factory(*args):
        raise ExceptionGroup("task group", [WindowsTerminalUnavailable("private-token")])
        yield  # pragma: no cover -- 提供异步上下文管理器协议。

    asyncio.run(run_agent(Store(), {"run_id": "test-run", "generation": 1, "model_config": {}}, factory, None, 1))
    assert updates[0][:3] == ("test-run", 1, "failed")
    assert "Windows 终端 MCP 连接失败" in updates[0][3]["error"]
    assert "ExceptionGroup" not in updates[0][3]["error"]
    assert "private-token" not in updates[0][3]["error"]
