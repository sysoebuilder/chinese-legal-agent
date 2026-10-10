"""验证法律检索工具可通过 MCP 协议发现和调用。"""

import asyncio

from mcp import Client

from chinese_legal_agent.config import ConfigStore, get_secret
from chinese_legal_agent.mcp_server import server as server_module


def test_search_laws_mcp_registration_and_call(monkeypatch, tmp_path) -> None:
    lifecycle = []

    class FakeQdrantServer:
        async def start(self):
            lifecycle.append("start")

        async def close(self):
            lifecycle.append("close")

    monkeypatch.setattr(server_module, "QdrantServer", FakeQdrantServer)
    config_store = ConfigStore(tmp_path)
    config_store.update("secrets", {"keys": {"ZAI_API_KEY": "mcp-file-key"}})
    monkeypatch.setattr(server_module, "load_app_config", config_store.load)
    async def fake_search_laws(cases):
        assert get_secret("ZAI_API_KEY") == "mcp-file-key"
        return {"results": [{"issue": cases[0].issue, "laws": []}]}

    monkeypatch.setattr(server_module, "retrieve_laws", fake_search_laws)

    async def scenario() -> None:
        async with Client(server_module.server) as client:
            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == ["search_laws"]
            case_schema = tools.tools[0].input_schema["properties"]["retrieval_cases"]
            assert case_schema["minItems"] == 1
            assert case_schema["maxItems"] == 5
            assert case_schema["description"]
            retrieval_case = tools.tools[0].input_schema["$defs"]["RetrievalCase"]
            assert all(
                retrieval_case["properties"][field]["description"]
                for field in ("issue", "title_keywords", "content_keywords", "case_facts")
            )
            result = await client.call_tool(
                "search_laws",
                {
                    "retrieval_cases": [
                        {
                            "issue": "休息日加班",
                            "title_keywords": ["劳动法"],
                            "content_keywords": ["加班工资"],
                            "case_facts": "周六工作且没有补休",
                        }
                    ]
                },
            )
            assert result.is_error is False
            assert result.structured_content == {
                "results": [{"issue": "休息日加班", "laws": []}]
            }

    asyncio.run(scenario())
    assert lifecycle == ["start", "close"]


def test_qdrant_start_failure_cleans_up(monkeypatch) -> None:
    import pytest

    lifecycle = []

    class FailedQdrantServer:
        async def start(self):
            lifecycle.append("start")
            raise RuntimeError("Qdrant 启动失败")

        async def close(self):
            lifecycle.append("close")

    monkeypatch.setattr(server_module, "QdrantServer", FailedQdrantServer)

    async def scenario():
        with pytest.raises(RuntimeError, match="Qdrant 启动失败"):
            async with server_module.lifespan(server_module.server):
                pytest.fail("Qdrant 未就绪时不应接受调用")

    asyncio.run(scenario())
    assert lifecycle == ["start", "close"]
