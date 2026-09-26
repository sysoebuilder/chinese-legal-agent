"""Host 侧连接本地 stdio MCP Server 的最小客户端。

此模块只负责连接、发现和调用，不将工具注册到 Agent。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, Tool


@asynccontextmanager
async def connect_stdio_server(
    command: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    read_timeout_seconds: float = 30,
) -> AsyncIterator[Client]:
    """启动一个 Server，初始化会话，并在退出时关闭连接和子进程。"""
    if not command.strip():
        raise ValueError("Server 启动命令不能为空")
    if read_timeout_seconds <= 0:
        raise ValueError("读取超时必须大于 0")

    server = StdioServerParameters(
        command=command,
        args=args or [],
        env=env,
        cwd=cwd,
    )
    async with Client(server, read_timeout_seconds=read_timeout_seconds) as client:
        yield client


async def list_all_tools(client: Client) -> list[Tool]:
    """取完 Server 的全部工具列表，包括分页结果。"""
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor)
        tools.extend(result.tools)
        cursor = result.next_cursor
        if not cursor:
            return tools


async def call_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> CallToolResult:
    """调用一个 MCP 工具，保留 Server 返回的结构化结果或错误。"""
    if not name.strip():
        raise ValueError("工具名不能为空")
    return await client.call_tool(name, arguments=arguments or {})
