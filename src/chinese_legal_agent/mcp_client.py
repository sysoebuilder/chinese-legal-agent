"""Host 侧连接本地 stdio MCP Server 的最小客户端。

发现的工具转换为 LangChain StructuredTool，可直接交给 Agent。
"""

import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit

import httpx2
from langchain_core.tools import StructuredTool, ToolException
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from chinese_legal_agent.config.context import get_secret


class CloudServerConfig(TypedDict):
    url: str
    headers: NotRequired[dict[str, str]]
    read_timeout_seconds: NotRequired[float]
    trust_env: NotRequired[bool]


def resolve_headers(headers: Mapping[str, str], *, secrets=None) -> dict[str, str]:
    """把请求头中的 ${ENV_NAME} 替换为环境变量，缺失时报告配置错误。"""
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = secrets.get(name) if secrets is not None else get_secret(name)
        if not value:
            raise ValueError(f"MCP 请求头缺少环境变量：{name}")
        return value

    return {key: re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
            for key, value in headers.items()}


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


@asynccontextmanager
async def connect_cloud_server(
    url: str,
    *,
    read_timeout_seconds: float = 60,
    headers: Mapping[str, str] | None = None,
    trust_env: bool = True,
) -> AsyncIterator[Client]:
    """连接一个 HTTP MCP Server，退出上下文时关闭连接。"""
    if read_timeout_seconds <= 0:
        raise ValueError("读取超时必须大于 0")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP Server 的地址必须是完整 HTTP(S) URL")
    if headers or not trust_env:
        async with (
            httpx2.AsyncClient(
                headers=dict(headers or {}), timeout=read_timeout_seconds, trust_env=trust_env,
            ) as http_client,
            Client(
                streamable_http_client(url, http_client=http_client),
                read_timeout_seconds=read_timeout_seconds,
            ) as client,
        ):
            yield client
    else:
        async with Client(url, read_timeout_seconds=read_timeout_seconds) as client:
            yield client


@dataclass
class MCPConnections:
    """只在 connect_servers 上下文内可用的客户端和工具。"""
    clients: dict[str, Client]
    tools: list[StructuredTool]
    errors: dict[str, str]


@asynccontextmanager
async def connect_servers(
    *,
    local_servers: Mapping[str, StdioServerParameters] | None = None,
    cloud_servers: Mapping[str, CloudServerConfig] | None = None,
    read_timeout_seconds: float = 60,
) -> AsyncIterator[MCPConnections]:
    """统一注册本地与云端 Client；单个服务失败记录到 errors，其余继续。

    全部失败时返回空 clients/tools 和完整 errors。退出上下文关闭成功连接。
    """
    local_servers = local_servers or {}
    cloud_servers = cloud_servers or {}
    if not local_servers and not cloud_servers:
        raise ValueError("至少需要一个 MCP Server")
    if read_timeout_seconds <= 0:
        raise ValueError("读取超时必须大于 0")
    if local_servers.keys() & cloud_servers.keys():
        raise ValueError("本地与云端 Server 名称不能重复")
    for name in [*local_servers, *cloud_servers]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or "__" in name:
            raise ValueError("服务名称只能包含英文字母、数字、单下划线或连字符")

    async with AsyncExitStack() as stack:
        clients: dict[str, Client] = {}
        tools: list[StructuredTool] = []
        names: set[str] = set()
        errors: dict[str, str] = {}
        for name in [*local_servers, *cloud_servers]:
            try:
                async with AsyncExitStack() as server_stack:
                    if name in local_servers:
                        config = local_servers[name]
                        connection = connect_stdio_server(
                            config.command, config.args, env=config.env, cwd=config.cwd,
                            read_timeout_seconds=read_timeout_seconds,
                        )
                    else:
                        cloud_config = cloud_servers[name]
                        connection = connect_cloud_server(
                            cloud_config["url"],
                            read_timeout_seconds=cloud_config.get("read_timeout_seconds", read_timeout_seconds),
                            headers=resolve_headers(cloud_config.get("headers", {})),
                            trust_env=cloud_config.get("trust_env", True),
                        )
                    client = await server_stack.enter_async_context(connection)
                    server_tools = await list_all_tools(client)
                    server_names: set[str] = set()
                    for tool in server_tools:
                        tool.name = f"{name}__{tool.name}"
                        if tool.name in names or tool.name in server_names:
                            raise ValueError(f"重复的 MCP 工具名称：{tool.name}")
                        server_names.add(tool.name)
                    # 完整连接并发现成功后，才把资源转交给总上下文。
                    stack.push_async_exit(server_stack.pop_all())
                    clients[name] = client
                    tools.extend(server_tools)
                    names.update(server_names)
            except Exception as error:  # noqa: BLE001 -- 隔离单个 MCP 服务故障，其他连接继续可用。
                errors[name] = f"{type(error).__name__}: {error}"
        yield MCPConnections(clients=clients, tools=tools, errors=errors)


def make_langchain_tool(client: Client, tool: Tool) -> StructuredTool:
    """绑定当前会话；调用工具时 client 必须仍处于连接上下文中。"""
    async def invoke(**arguments: Any) -> str:
        result = await call_tool(client, tool.name, arguments)
        text = "\n".join(getattr(item, "text", str(item)) for item in result.content)
        if result.is_error:
            raise ToolException(text or f"MCP 工具 {tool.name} 执行失败")
        if result.structured_content is not None:
            return json.dumps(result.structured_content, ensure_ascii=False)
        return text

    return StructuredTool.from_function(
        coroutine=invoke,
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=tool.input_schema,
        handle_tool_error=True,
        metadata={
            "source": "mcp",
            "risk_level": "low" if getattr(getattr(tool, "annotations", None), "read_only_hint", False) else "high",
        },
    )


async def list_all_tools(client: Client) -> list[StructuredTool]:
    """取完全部分页，返回可直接传给 create_agent(tools=...) 的工具。"""
    tools: list[StructuredTool] = []
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor)
        tools.extend(make_langchain_tool(client, tool) for tool in result.tools)
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
