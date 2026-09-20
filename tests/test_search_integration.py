import asyncio
import json
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from ai_agent_learning.agent.context import ContextManager
from ai_agent_learning.agent.loop import AgentLoop
from ai_agent_learning.agent.policy import AgentLimits, AgentPolicy
from ai_agent_learning.model import GlmModelClient
from ai_agent_learning.planning.executor import PlanExecutor
from ai_agent_learning.planning.planner import Planner
from ai_agent_learning.planning.state import PlanExecution, PlanStatus, StepExecution
from ai_agent_learning.schema import Message, ModelRequest
from ai_agent_learning.tools.builtin.web_search import WebSearchClient
from ai_agent_learning.tools.catalog import build_builtin_tools
from ai_agent_learning.tools.executor import ToolExecutor
from ai_agent_learning.tools.registry import ToolRegistry

REQUEST = ModelRequest(
    model="test-model",
    messages=[Message(role="user", content="搜索智谱官方文档并总结")],
)
SEARCH_RESULT = {
    "search_result": [
        {
            "title": "官方文档",
            "link": "https://docs.bigmodel.cn/",
            "content": "GLM 文档说明",
        }
    ]
}


def model_response(text=None, call=False):
    arguments = json.dumps({"query": "智谱官方文档"}, ensure_ascii=False)
    message = {"role": "assistant", "content": text}
    if call:
        message.update(
            reasoning_content="需要先查官方资料",
            tool_calls=[
                {
                    "id": "call-search",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": arguments},
                }
            ],
        )
    return {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 0,
        "model": REQUEST.model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
    }

def stream_response(response):
    response["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    choice["delta"] = choice.pop("message")
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n",
    )


async def make_client(handler):
    client = GlmModelClient(api_key="model-key", base_url="https://model.test/api/")
    await client.close()
    client._http_client = httpx.AsyncClient(
        base_url="https://model.test/api/",
        transport=httpx.MockTransport(handler),
    )
    client._client = AsyncOpenAI(
        api_key="model-key",
        base_url="https://model.test/api/",
        http_client=client._http_client,
        max_retries=0,
    )
    return client


def test_agent_search_round_trip_uses_registry_and_records_results():
    bodies = []
    searches = []

    def model_handler(request):
        if request.url.path.endswith(("tokenization", "tokenizer")):
            return httpx.Response(
                200,
                json={"usage": {"total_tokens": 10}, "data": [{"total_tokens": 10}]},
            )
        body = json.loads(request.content)
        bodies.append(body)
        assert len(body["tools"]) == 6
        assert all(tool["type"] == "function" for tool in body["tools"])
        tool = next(
            item for item in body["tools"]
            if item["function"]["name"]
            == "web_search"
        )
        function = tool["function"]
        assert function["name"] == "web_search"
        assert function["parameters"]["required"] == ["query"]
        if len(bodies) == 2:
            assert body["messages"][-2]["reasoning_content"] == "需要先查官方资料"
            result = json.loads(body["messages"][-1]["content"])
            assert result["success"]
            assert result["data"]["results"][0]["url"] == "https://docs.bigmodel.cn/"
        assert body["stream"] is True
        chunk = model_response("文档总结", call=len(bodies) == 1)
        chunk["object"] = "chat.completion.chunk"
        choice = chunk["choices"][0]
        choice["delta"] = choice.pop("message")
        for index, call in enumerate(choice["delta"].get("tool_calls", [])):
            call["index"] = index
        choice["finish_reason"] = "tool_calls" if len(bodies) == 1 else "stop"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    def search_handler(request):
        searches.append(json.loads(request.content))
        return httpx.Response(200, json=SEARCH_RESULT)

    async def run():
        client = await make_client(model_handler)
        try:
            async with httpx.AsyncClient(
                base_url="https://search.test/",
                transport=httpx.MockTransport(search_handler),
            ) as http_client:
                with patch(
                    "ai_agent_learning.tools.builtin.web_search.handler",
                    WebSearchClient(http_client).handler,
                ):
                    registry = ToolRegistry(tools=build_builtin_tools())
                loop = AgentLoop(
                    model_client=client,
                    registry=registry,
                    executor=ToolExecutor(registry),
                    context_manager=ContextManager(client, REQUEST.model),
                    policy=AgentPolicy(AgentLimits(max_steps=3)),
                )
                result = await loop.run(REQUEST.model_copy(deep=True))
                assert result.response.message.content == "文档总结"
                assert result.state.tool_calls_used == 1
                assert (
                    result.state.steps[0].tool_results[0].metadata["tool_name"]
                    == "web_search"
                )
                assert len(searches) == 1
        finally:
            await client.close()

    asyncio.run(run())


def test_planner_search_step_then_model_step():
    plan = {
        "goal": "搜索并总结",
        "steps": [
            {
                "step_id": "step_1",
                "title": "搜索",
                "executor": {"type": "tool", "tool_name": "web_search"},
                "inputs": {"query": "智谱官方文档"},
                "expected_result": "资料",
                "completion_condition": "获取资料",
                "depends_on": [],
            },
            {
                "step_id": "step_2",
                "title": "总结",
                "executor": {"type": "model"},
                "inputs": {},
                "expected_result": "总结",
                "completion_condition": "生成总结",
                "depends_on": ["step_1"],
            },
        ],
    }
    bodies = []
    searches = []

    def model_handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert "tools" not in body and "tool_choice" not in body
        messages = body["messages"]
        if len(bodies) == 1:
            assert "web_search" in messages[0]["content"]
            return stream_response(model_response(json.dumps(plan)))
        payload = json.loads(messages[-1]["content"])
        assert (
            payload["dependency_results"]["step_1"]["results"][0]["url"]
            == "https://docs.bigmodel.cn/"
        )
        return stream_response(model_response("带来源的总结"))

    def search_handler(request):
        searches.append(request)
        return httpx.Response(200, json=SEARCH_RESULT)

    async def run():
        client = await make_client(model_handler)
        try:
            async with httpx.AsyncClient(
                base_url="https://search.test/",
                transport=httpx.MockTransport(search_handler),
            ) as http_client:
                with patch(
                    "ai_agent_learning.tools.builtin.web_search.handler",
                    WebSearchClient(http_client).handler,
                ):
                    registry = ToolRegistry(tools=build_builtin_tools())
                parsed = await Planner(client, registry).create_plan(REQUEST)
                assert not searches  # 生成计划阶段不能触发搜索。
                execution = PlanExecution(
                    plan=parsed,
                    status=PlanStatus.RUNNING,
                    steps=[
                        StepExecution(step_id=step.step_id) for step in parsed.steps
                    ],
                )
                executor = PlanExecutor(client, REQUEST.model, ToolExecutor(registry))
                result = await executor.execute(execution)
                assert result.status is PlanStatus.COMPLETED
                assert result.steps[1].result == "带来源的总结"
                assert len(searches) == 1
        finally:
            await client.close()

    asyncio.run(run())
