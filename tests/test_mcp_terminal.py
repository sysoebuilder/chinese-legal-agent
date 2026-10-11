"""Windows 终端的 MCP 发现、参数校验和 HTTP 认证。"""

import asyncio

import pytest
from mcp import Client
from starlette.testclient import TestClient

from chinese_legal_agent import mcp_terminal


def test_terminal_registration_delegates_and_validates(monkeypatch):
    calls = []

    async def handler(command, working_directory, timeout_seconds):
        calls.append((command, working_directory, timeout_seconds))
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False}

    monkeypatch.setattr(mcp_terminal, "handler", handler)

    async def scenario():
        async with Client(mcp_terminal.server) as client:
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == ["run_command"]
            assert tools[0].annotations.read_only_hint is False
            result = await client.call_tool("run_command", {
                "command": "Write-Output ok", "working_directory": "E:\\project",
            })
            assert result.is_error is False
            assert result.structured_content["stdout"] == "ok"
            invalid = await client.call_tool("run_command", {
                "command": "test", "working_directory": "E:\\project", "timeout_seconds": 301,
            })
            assert invalid.is_error is True

    asyncio.run(scenario())
    assert calls == [("Write-Output ok", "E:\\project", 30)]


def test_terminal_http_auth_and_host_validation():
    with pytest.raises(ValueError, match="WINDOWS_MCP_TOKEN"):
        mcp_terminal.create_app(" ")
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    }
    with TestClient(mcp_terminal.create_app("test-token"), base_url="http://127.0.0.1:8765") as client:
        headers = {"Accept": "application/json, text/event-stream"}
        assert client.get("/healthz").status_code == 401
        health = client.get("/healthz", headers={"Authorization": "Bearer test-token"})
        assert health.json() == {"service": "agent100-windows-terminal"}
        assert client.post("/mcp", json=init, headers=headers).status_code == 401
        headers["Authorization"] = "Bearer wrong"
        assert client.post("/mcp", json=init, headers=headers).status_code == 401
        headers["Authorization"] = "Bearer test-token"
        assert client.post("/mcp", json=init, headers=headers).status_code == 200
        headers["Host"] = "host.docker.internal:8765"
        assert client.post("/mcp", json=init, headers=headers).status_code == 200
        headers["Host"] = "evil.example:8765"
        assert client.post("/mcp", json=init, headers=headers).status_code == 421
