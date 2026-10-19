import asyncio
import json
from collections import deque

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from chinese_legal_agent.langchain_agent.legal.graph import (
    _route_decision,
    _route_uncertain_answer,
    create_legal_graph,
)
from chinese_legal_agent.langchain_agent.legal.nodes import LegalNodes, MAX_RETRIEVAL_ROUNDS
from chinese_legal_agent.langchain_agent.legal.schemas import (
    LegalAnswerReviewOutput,
    LegalDecisionOutput,
)


LAW = {
    "issue": "劳动合同解除",
    "id": "law-1",
    "payload": {"title": "测试法律依据", "content": "仅用于验证法律上下文传递。"},
    "bm25_score": None,
    "vector_score": None,
}
POINTS = ["公司是否已经实施解除尚不明确，会影响赔偿请求是否具备前提。"]


class ScriptModel:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    async def astream(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = self.responses.popleft()
        if isinstance(response, dict):
            assert kwargs["response_format"] == {"type": "json_object"}
            content = json.dumps(response, ensure_ascii=False)
        else:
            assert "response_format" not in kwargs
            content = response
        middle = len(content) // 2
        yield AIMessageChunk(content=content[:middle])
        yield AIMessageChunk(content=content[middle:])


class LawTool:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, arguments):
        self.calls += 1
        return {"results": [{"issue": LAW["issue"], "laws": [LAW]}]}


def state_with_laws():
    case = LegalNodes._new_case("经理说能力不行要辞退，我能直接要求2N吗？")
    case["retrieved_laws"] = [LAW]
    case["usable_laws"] = [LAW]
    case["asked_questions"] = ["公司是否已经明确解除劳动合同？"]
    case["user_supplements"] = ["我无法进一步确认。"]
    case["current_question"] = case["user_supplements"][-1]
    case["objective_facts"] = ["用户收到领导微信消息。"]
    return {
        "active_case_id": "case_1", "cases": {"case_1": case},
        "messages": [HumanMessage(content=case["original_question"])],
    }


@pytest.mark.parametrize("via_review", [False, True])
def test_uncertain_answer_survives_checkpoint_and_returns_as_final_answer(via_review):
    question = "公司是否已经明确解除劳动合同？"
    answer = "若已发生解除，应结合依据判断；若尚未解除，不能把将来可能辞退当作已解除。"
    responses = [
        {"case_action": "new"},
        {"objective_facts": ["用户收到领导微信消息。"]},
        {"action": "retrieve_more", "reason": "需要法律依据", "retrieval_cases": [{
            "issue": LAW["issue"], "title_keywords": [], "content_keywords": ["解除"],
            "case_facts": "用户收到领导微信消息。",
        }]},
        {"action": "ask_user", "reason": "解除事实未明", "question": question},
        {"objective_facts": ["用户收到领导微信消息，无法确认是否已解除。"]},
    ]
    uncertain = {"action": "uncertain_answer", "reason": "无法进一步核实事实", "uncertainty_points": POINTS}
    if via_review:
        responses.extend([
            {"action": "final_answer", "reason": "已有依据", "usable_law_ids": ["law-1"]},
            uncertain,
        ])
    else:
        responses.append({**uncertain, "usable_law_ids": ["law-1"]})
    responses.append(answer)
    model, tool, saver = ScriptModel(responses), LawTool(), InMemorySaver()
    config = {"configurable": {"thread_id": f"uncertain-{via_review}"}}
    other_case = LegalNodes._new_case("另一件独立案件")
    other_case["uncertainty_points"] = ["仅属于另一案件的不确定点"]

    def build(pause=False):
        return create_legal_graph(model, law_tool=tool).builder.compile(
            checkpointer=saver,
            interrupt_before=["generate_uncertain_answer"] if pause else None,
        )

    async def run():
        graph = build(pause=True)
        paused = await graph.ainvoke({
            "messages": [HumanMessage(content="经理说能力不行要辞退，我能直接要求2N吗？")],
            "cases": {"case_1": other_case},
        }, config)
        assert paused["__interrupt__"][0].value["question"] == question
        paused = await graph.ainvoke(Command(resume="我无法进一步确认。"), config)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("generate_uncertain_answer",)
        case = snapshot.values["cases"]["case_2"]
        assert case["uncertainty_points"] == POINTS
        assert case["usable_laws"] == [LAW]
        assert case["final_answer"] is None
        assert case["refusal_reason"] is None

        final = await build().ainvoke(None, config)
        case = final["cases"]["case_2"]
        assert case["final_answer"] == answer
        assert case["uncertainty_points"] == POINTS
        assert case["asked_questions"] == [question]
        assert case["refusal_reason"] is None
        assert isinstance(final["messages"][-1], AIMessage)
        assert final["messages"][-1].content == answer
        assert final["cases"]["case_1"] == other_case

        context = json.loads(model.calls[-1][0][1].content.split("\n", 1)[1])
        assert context == LegalNodes._answer_context(final)
        assert context["original_question"] != context["current_question"]
        assert context["user_supplements"] == ["我无法进一步确认。"]
        assert context["uncertainty_points"] == POINTS
        assert not model.responses
        assert tool.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("ids,points", [([], POINTS), (["law-1"], []), (["law-1"], [" "])])
def test_uncertain_decision_requires_law_ids_and_specific_points(ids, points):
    with pytest.raises(ValidationError):
        LegalDecisionOutput(
            action="uncertain_answer", reason="事实不明",
            usable_law_ids=ids, uncertainty_points=points,
        )


def test_uncertain_review_requires_specific_points():
    with pytest.raises(ValidationError):
        LegalAnswerReviewOutput(action="uncertain_answer", reason="事实不明")


@pytest.mark.parametrize("ids", [["not-retrieved"], ["law-1", "not-retrieved"]])
def test_uncertain_decision_cannot_use_unretrieved_law_ids(ids):
    state = state_with_laws()
    model = ScriptModel([{
        "action": "uncertain_answer", "reason": "事实不明",
        "usable_law_ids": ids, "uncertainty_points": POINTS,
    }])
    update = asyncio.run(LegalNodes(model).decide(state))
    updated = {**state, **update}
    assert _route_decision(updated) == "refuse"
    assert updated["cases"]["case_1"]["uncertainty_points"] == []
    assert updated["cases"]["case_1"]["usable_laws"] == []


@pytest.mark.parametrize("missing", ["usable_laws", "uncertainty_points"])
def test_uncertain_generation_requires_laws_and_points(missing):
    state = state_with_laws()
    state["cases"]["case_1"]["uncertainty_points"] = POINTS
    state["cases"]["case_1"][missing] = []
    model = ScriptModel([])
    update = asyncio.run(LegalNodes(model).generate_uncertain_answer(state))
    assert _route_uncertain_answer({**state, **update}) == "refuse"
    assert not model.calls


def test_uncertain_review_is_allowed_at_retrieval_limit():
    state = state_with_laws()
    state["cases"]["case_1"]["retrieval_round"] = MAX_RETRIEVAL_ROUNDS
    model = ScriptModel([{
        "action": "uncertain_answer", "reason": "法律足够，事实无法核实",
        "uncertainty_points": POINTS,
    }])
    update = asyncio.run(LegalNodes(model).generate_final_answer(state))
    assert update["cases"]["case_1"]["decision"]["action"] == "uncertain_answer"
    assert update["cases"]["case_1"]["usable_laws"] == [LAW]
    assert update["cases"]["case_1"]["refusal_reason"] is None


def test_uncertain_stream_failure_retries_without_redeciding_or_losing_points():
    state = state_with_laws()
    state["cases"]["case_1"]["uncertainty_points"] = POINTS

    class FailingModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="未完成的回答片段")
            raise TimeoutError("temporary failure")

    nodes = LegalNodes(FailingModel())
    first_update = asyncio.run(nodes.generate_uncertain_answer(state))
    first = {**state, **first_update}
    assert _route_uncertain_answer(first) == "retry"
    assert first["cases"]["case_1"]["uncertainty_points"] == POINTS
    assert first["cases"]["case_1"]["final_answer"] is None
    assert "messages" not in first_update
    final = {**first, **asyncio.run(nodes.generate_uncertain_answer(first))}
    assert _route_uncertain_answer(final) == "refuse"


def test_new_supplement_clears_stale_uncertainties_without_clearing_question_history():
    state = state_with_laws()
    state["cases"]["case_1"]["uncertainty_points"] = POINTS
    state["messages"].append(HumanMessage(content="公司昨天已书面通知解除。"))
    nodes = LegalNodes(ScriptModel([{"case_action": "continue", "case_id": "case_1"}]))
    update = asyncio.run(nodes.classify(state))
    case = update["cases"]["case_1"]
    assert case["uncertainty_points"] == []
    assert case["asked_questions"] == state["cases"]["case_1"]["asked_questions"]


def test_legacy_case_without_uncertainties_can_still_generate_normal_answer():
    state = state_with_laws()
    del state["cases"]["case_1"]["uncertainty_points"]
    model = ScriptModel([{"action": "answer", "reason": "可以解释已有事实"}, "普通最终回答"])
    update = asyncio.run(LegalNodes(model).generate_final_answer(state))
    assert update["cases"]["case_1"]["final_answer"] == "普通最终回答"
    assert update["cases"]["case_1"]["uncertainty_points"] == []
    context = json.loads(model.calls[-1][0][1].content.split("\n", 1)[1])
    assert set(context) == set(LegalNodes._answer_context(state))
    assert context["uncertainty_points"] == []
