"""验证 MCP 分页发现和 LangChain 工具绑定。"""

import asyncio
import json
from types import SimpleNamespace

from langchain_core.tools import StructuredTool

from chinese_legal_agent.mcp_client import list_all_tools


def test_list_all_tools_binds_each_page_and_handles_errors():
    calls = []

    class FakeClient:
        async def list_tools(self, cursor=None):
            name = "first" if cursor is None else "second"
            return SimpleNamespace(
                tools=[SimpleNamespace(
                    name=name,
                    description=None,
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                )],
                next_cursor="page2" if cursor is None else None,
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            failed = arguments["query"] == "fail"
            return SimpleNamespace(
                is_error=failed,
                content=[SimpleNamespace(text="检索失败" if failed else "plain text")],
                structured_content=None if failed else {"name": name},
            )

    async def scenario():
        tools = await list_all_tools(FakeClient())
        assert all(isinstance(tool, StructuredTool) for tool in tools)
        assert [tool.name for tool in tools] == ["first", "second"]
        for tool in tools:
            assert json.loads(await tool.ainvoke({"query": "test"})) == {"name": tool.name}
        assert await tools[1].ainvoke({"query": "fail"}) == "检索失败"

    asyncio.run(scenario())
    assert calls == [("first", {"query": "test"}), ("second", {"query": "test"}), ("second", {"query": "fail"})]


def test_cloud_servers_route_same_named_tools_and_close(monkeypatch):
    from chinese_legal_agent import mcp_client

    events = []

    class FakeClient:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            events.append(("open", self.url))
            return self

        async def __aexit__(self, *args):
            events.append(("close", self.url))

        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[SimpleNamespace(
                name="search", description="Search", input_schema={"type": "object", "properties": {}}
            )], next_cursor=None)

        async def call_tool(self, name, arguments):
            assert name == "search"
            return SimpleNamespace(is_error=False, content=[], structured_content={"server": self.url})

    monkeypatch.setattr(mcp_client, "Client", FakeClient)

    async def scenario():
        async with mcp_client.connect_servers(cloud_servers={"a": {"url": "https://a.example/mcp"}, "b": {"url": "https://b.example/mcp"}}) as connections:
            tools = connections.tools
            assert list(connections.clients) == ["a", "b"]
            assert [tool.name for tool in tools] == ["a__search", "b__search"]
            assert json.loads(await tools[0].ainvoke({}))["server"] == "https://a.example/mcp"
            assert json.loads(await tools[1].ainvoke({}))["server"] == "https://b.example/mcp"
        assert events[-2:] == [("close", "https://b.example/mcp"), ("close", "https://a.example/mcp")]

    asyncio.run(scenario())


def test_cloud_servers_discovery_failure_preserves_other_connections(monkeypatch):
    from chinese_legal_agent import mcp_client

    closed = []

    class FakeClient:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self.url)

        async def list_tools(self, cursor=None):
            if "b.example" in self.url:
                raise RuntimeError("发现失败")
            return SimpleNamespace(tools=[], next_cursor=None)

    monkeypatch.setattr(mcp_client, "Client", FakeClient)

    async def scenario():
        async with mcp_client.connect_servers(cloud_servers={"a": {"url": "https://a.example/mcp"}, "b": {"url": "https://b.example/mcp"}, "c": {"url": "https://c.example/mcp"}}) as connections:
            assert list(connections.clients) == ["a", "c"]
            assert connections.errors == {"b": "RuntimeError: 发现失败"}
            assert closed == ["https://b.example/mcp"]

    asyncio.run(scenario())
    assert closed == ["https://b.example/mcp", "https://c.example/mcp", "https://a.example/mcp"]


def test_mixed_servers_connection_failure_preserves_local(monkeypatch):
    from mcp import StdioServerParameters

    from chinese_legal_agent import mcp_client

    closed = []

    class FakeClient:
        def __init__(self, server, **kwargs):
            self.server = server

        async def __aenter__(self):
            if isinstance(self.server, str):
                raise RuntimeError("云端连接失败")
            assert self.server.command == "python"
            assert self.server.args == ["local_server.py"]
            return self

        async def __aexit__(self, *args):
            closed.append(self.server.command)

        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[], next_cursor=None)

    monkeypatch.setattr(mcp_client, "Client", FakeClient)

    async def scenario():
        async with mcp_client.connect_servers(
            local_servers={"local": StdioServerParameters(command="python", args=["local_server.py"])},
            cloud_servers={"cloud": {"url": "https://cloud.example/mcp"}},
        ) as connections:
            assert list(connections.clients) == ["local"]
            assert connections.errors == {"cloud": "RuntimeError: 云端连接失败"}
            assert closed == []
        async with mcp_client.connect_servers(cloud_servers={"cloud": {"url": "https://cloud.example/mcp"}}) as connections:
            assert connections.clients == {}
            assert connections.tools == []
            assert "cloud" in connections.errors

    asyncio.run(scenario())
    assert closed == ["python"]


def test_cloud_server_passes_auth_headers_and_closes_http_client(monkeypatch):
    from chinese_legal_agent import mcp_client

    observed = {}

    def fake_transport(url, *, http_client):
        observed["http_client"] = http_client
        assert url == "https://api.githubcopilot.com/mcp/"
        assert http_client.headers["Authorization"] == "Bearer test-token"
        return object()

    class FakeClient:
        def __init__(self, server, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] = True

    monkeypatch.setattr(mcp_client, "streamable_http_client", fake_transport)
    monkeypatch.setattr(mcp_client, "Client", FakeClient)

    async def scenario():
        async with mcp_client.connect_cloud_server(
            "https://api.githubcopilot.com/mcp/", headers={"Authorization": "Bearer test-token"}
        ):
            assert not observed["http_client"].is_closed
        assert observed["closed"]
        assert observed["http_client"].is_closed

    asyncio.run(scenario())


def test_connect_servers_uses_headers_from_cloud_config(monkeypatch):
    from contextlib import asynccontextmanager

    from chinese_legal_agent import mcp_client

    observed = []
    monkeypatch.setenv("TEST_MCP_TOKEN", "test-token")
    monkeypatch.delenv("TEST_MCP_MISSING", raising=False)

    @asynccontextmanager
    async def fake_connect(url, *, headers, **kwargs):
        observed.append((url, headers, kwargs))
        yield object()

    async def fake_tools(client):
        return []

    monkeypatch.setattr(mcp_client, "connect_cloud_server", fake_connect)
    monkeypatch.setattr(mcp_client, "list_all_tools", fake_tools)

    async def scenario():
        async with mcp_client.connect_servers(cloud_servers={
            "missing": {"url": "https://missing.example/mcp", "headers": {"Authorization": "Bearer ${TEST_MCP_MISSING}"}},
            "github": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer ${TEST_MCP_TOKEN}", "X-Custom": "custom"}, "read_timeout_seconds": 330, "trust_env": False},
        }) as connections:
            assert list(connections.clients) == ["github"]
            assert "TEST_MCP_MISSING" in connections.errors["missing"]

    asyncio.run(scenario())
    assert observed == [("https://api.githubcopilot.com/mcp/", {"Authorization": "Bearer test-token", "X-Custom": "custom"}, {"read_timeout_seconds": 330, "trust_env": False})]
