import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from openai import APIStatusError

from ai_agent_learning.langchain_agent.legal.graph import (
    _route_decision,
    _route_final_answer,
    _route_retrieval,
)
from ai_agent_learning.langchain_agent.legal.nodes import LegalNodes
from ai_agent_learning.langchain_agent.legal.schemas import LegalAnswerReviewOutput


def _state():
    case = LegalNodes._new_case("周六加班费怎么算？")
    return {
        "messages": [HumanMessage(content=case["current_question"])],
        "active_case_id": "case_1",
        "cases": {"case_1": case},
    }


def _apply(state, update):
    return {**state, **update}


def test_classification_and_fact_errors_escape_to_chat():
    nodes = LegalNodes(model=None)

    async def fail(*args, **kwargs):
        raise TimeoutError("temporary failure")

    nodes._invoke_json = fail
    state = _state()
    with pytest.raises(TimeoutError):
        asyncio.run(nodes.classify(state))
    with pytest.raises(TimeoutError):
        asyncio.run(nodes.analyze_case(state))


def test_missing_retrieval_keywords_returns_to_decide_with_feedback():
    nodes = LegalNodes(model=None)
    state = _state()
    update = asyncio.run(nodes.retrieve_laws(state))
    retried = _apply(state, update)

    assert _route_retrieval(retried) == "decide"
    assert retried["cases"]["case_1"]["invalid_retrieval_count"] == 1
    assert "读取不到有效的检索关键词" in nodes._decision_context(retried)


def test_decision_error_retries_in_subgraph_then_refuses():
    nodes = LegalNodes(model=None)

    async def fail(*args, **kwargs):
        raise TimeoutError("temporary failure")

    nodes._invoke_json = fail
    state = _state()
    first = _apply(state, asyncio.run(nodes.decide(state)))
    assert _route_decision(first) == "retry"
    assert first["cases"]["case_1"]["last_node_error"] == "decide：TimeoutError"

    second = _apply(first, asyncio.run(nodes.decide(first)))
    assert _route_decision(second) == "refuse"
    assert second["cases"]["case_1"]["retry_target"] is None


def test_decision_stream_collects_json_for_validation():
    class StreamingModel:
        async def astream(self, messages, *, config, response_format):
            assert config == {"tags": ["legal_structured"]}
            assert response_format == {"type": "json_object"}
            yield AIMessageChunk(content="{\"action\":\"ask_user\",")
            yield AIMessageChunk(content='"reason":"缺少时间事实","question":"请说明离职日期。"}')

    nodes = LegalNodes(model=StreamingModel())
    state = _state()
    updated = _apply(state, asyncio.run(nodes.decide(state)))

    assert _route_decision(updated) == "ask_user"
    assert updated["cases"]["case_1"]["decision"]["question"] == "请说明离职日期。"


def test_final_answer_review_and_generation_errors_retry_in_subgraph():
    nodes = LegalNodes(model=None)
    state = _state()
    state["cases"]["case_1"]["usable_laws"] = [
        {
            "issue": "加班费",
            "id": "law-1",
            "payload": {"title": "劳动法"},
            "bm25_score": None,
            "vector_score": None,
        }
    ]

    async def fail_review(*args):
        raise TimeoutError("review timeout")

    nodes._invoke_json = fail_review
    review_state = _apply(state, asyncio.run(nodes.generate_final_answer(state)))
    assert _route_final_answer(review_state) == "retry"

    async def pass_review(*args):
        return LegalAnswerReviewOutput(action="answer", reason="依据充分")

    class FailingModel:
        async def astream(self, messages):
            raise TimeoutError("generation timeout")
            yield

    nodes._invoke_json = pass_review
    nodes.model = FailingModel()
    generation_state = _apply(state, asyncio.run(nodes.generate_final_answer(state)))
    assert _route_final_answer(generation_state) == "retry"


def test_insufficient_balance_status_does_not_retry_in_subgraph():
    nodes = LegalNodes(model=None)
    request = httpx.Request("POST", "https://api.test/chat/completions")
    response = httpx.Response(402, request=request)

    async def fail(*args, **kwargs):
        raise APIStatusError("insufficient balance", response=response, body=None)

    nodes._invoke_json = fail
    state = _state()
    updated = _apply(state, asyncio.run(nodes.decide(state)))

    assert _route_decision(updated) == "refuse"
    assert updated["cases"]["case_1"]["retry_target"] is None
    assert "HTTP 402" in updated["cases"]["case_1"]["refusal_reason"]
