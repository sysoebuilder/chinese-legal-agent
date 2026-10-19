import asyncio
import json

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from chinese_legal_agent.langchain_agent.legal.graph import create_legal_graph
from chinese_legal_agent.langchain_agent.legal.nodes import LegalNodes
from chinese_legal_agent.langchain_agent.legal.schemas import (
    LegalAnswerReviewOutput,
    LegalCaseAnalysisOutput,
    LegalClassificationOutput,
    LegalDecisionOutput,
)


def test_question_history_is_checkpointed_before_interrupt_and_not_duplicated_on_resume(
    monkeypatch,
):
    questions = ["是否已经归还过部分借款？", "是否有能够核实还款的银行记录？"]
    contexts = []

    async def invoke(self, schema, system, content, *, stream=False):
        context = json.loads(content)
        if schema is LegalClassificationOutput:
            return LegalClassificationOutput(case_action="new")
        if schema is LegalCaseAnalysisOutput:
            return LegalCaseAnalysisOutput(
                objective_facts=[context["original_question"], *context["user_supplements"]]
            )
        assert schema is LegalDecisionOutput
        contexts.append(context)
        count = len(context["asked_questions"])
        if count < len(questions):
            return LegalDecisionOutput(
                action="ask_user", reason="尚有关键事实未核实", question=questions[count]
            )
        return LegalDecisionOutput(action="refuse", reason="用户无法核实剩余本金")

    monkeypatch.setattr(LegalNodes, "_invoke_json", invoke)
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "question-history"}}
    other_case = LegalNodes._new_case("另一起劳动争议")
    other_case["asked_questions"] = ["公司是否安排培训？"]

    def build():
        return create_legal_graph(model=None).builder.compile(checkpointer=saver)

    async def run():
        graph = build()
        paused = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="我借给朋友五万元，能要求还钱吗？")],
                "cases": {"case_1": other_case},
            },
            config=config,
        )
        assert paused["__interrupt__"][0].value["question"] == questions[0]
        snapshot = await graph.aget_state(config)
        assert snapshot.values["cases"]["case_2"]["asked_questions"] == questions[:1]
        assert snapshot.values["cases"]["case_2"]["user_supplements"] == []

        # 重新构建图再恢复，验证历史来自 checkpoint，而不是节点实例内存。
        graph = build()
        paused_again = await graph.ainvoke(Command(resume="记不清了"), config=config)
        assert paused_again["__interrupt__"][0].value["question"] == questions[1]
        assert paused_again["cases"]["case_2"]["asked_questions"] == questions
        assert paused_again["cases"]["case_2"]["user_supplements"] == ["记不清了"]

        final = await build().ainvoke(Command(resume="无法提供记录"), config=config)
        case = final["cases"]["case_2"]
        assert case["asked_questions"] == questions
        assert case["user_supplements"] == ["记不清了", "无法提供记录"]
        assert case["decision"]["action"] == "refuse"
        assert final["cases"]["case_1"] == other_case

    asyncio.run(run())
    assert [context["asked_questions"] for context in contexts] == [
        [], questions[:1], questions
    ]
    assert "记不清了" in contexts[1]["objective_facts"]
    assert "无法提供记录" in contexts[2]["objective_facts"]


def test_final_review_question_is_recorded_and_history_is_passed_to_review():
    nodes = LegalNodes(model=None)
    case = LegalNodes._new_case("公司能否辞退我？")
    case["asked_questions"] = ["公司是否安排过培训？"]
    case["user_supplements"] = ["没有"]
    case["usable_laws"] = [{
        "issue": "解除劳动合同",
        "id": "law-1",
        "payload": {"title": "劳动合同法"},
        "bm25_score": None,
        "vector_score": None,
    }]
    state = {"active_case_id": "case_1", "cases": {"case_1": case}}

    async def review(schema, system, content, *, stream=False):
        assert schema is LegalAnswerReviewOutput
        context = json.loads(content)
        assert context["asked_questions"] == ["公司是否安排过培训？"]
        assert context["user_supplements"] == ["没有"]
        return LegalAnswerReviewOutput(
            action="ask_user", reason="解除依据不明", question="解除通知写明了什么理由？"
        )

    nodes._invoke_json = review
    result = asyncio.run(nodes.generate_final_answer(state))
    assert result["cases"]["case_1"]["asked_questions"] == [
        "公司是否安排过培训？", "解除通知写明了什么理由？"
    ]
    assert case["asked_questions"] == ["公司是否安排过培训？"]


def test_legacy_case_without_history_can_decide_and_continue():
    nodes = LegalNodes(model=None)
    case = LegalNodes._new_case("公司能否辞退我？")
    del case["asked_questions"]
    state = {
        "active_case_id": "case_1", "cases": {"case_1": case},
        "messages": [HumanMessage(content="补充工资信息")],
    }
    assert json.loads(nodes._decision_context(state))["asked_questions"] == []

    async def ask(schema, system, content, *, stream=False):
        return LegalDecisionOutput(
            action="ask_user", reason="解除依据不明", question="公司解除的理由是什么？"
        )

    nodes._invoke_json = ask
    decided = asyncio.run(nodes.decide(state))
    assert decided["cases"]["case_1"]["asked_questions"] == ["公司解除的理由是什么？"]

    async def classify(schema, system, content, *, stream=False):
        return LegalClassificationOutput(case_action="continue", case_id="case_1")

    nodes._invoke_json = classify
    continued = asyncio.run(nodes.classify({**state, **decided}))
    assert continued["cases"]["case_1"]["asked_questions"] == ["公司解除的理由是什么？"]
