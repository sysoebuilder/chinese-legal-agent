"""验证法律检索工具可通过 MCP 协议发现和调用。"""

import asyncio

from mcp import Client

from ai_agent_learning.mcp_server import server as server_module


def test_search_raws_mcp_registration_and_call(monkeypatch) -> None:
    async def fake_search_laws(cases):
        return {"results": [{"issue": cases[0].issue, "laws": []}]}

    monkeypatch.setattr(server_module, "search_laws", fake_search_laws)

    async def scenario() -> None:
        async with Client(server_module.server) as client:
            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == ["search_raws"]
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
                "search_raws",
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
