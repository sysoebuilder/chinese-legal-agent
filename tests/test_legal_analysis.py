import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from chinese_legal_agent.langchain_agent.legal.nodes import LegalNodes
from chinese_legal_agent.langchain_agent.legal.schemas import LegalCaseAnalysisOutput


def test_classify_receives_optional_previous_ai_message():
    nodes = LegalNodes(model=None)
    seen = []

    async def fake_invoke(schema, system, content, *, stream=False):
        seen.append(json.loads(content))
        return SimpleNamespace(case_action="none", case_id=None)

    nodes._invoke_json = fake_invoke
    asyncio.run(nodes.classify({"messages": [
        HumanMessage(content="周六加班费怎么算？"),
        AIMessage(content="要看是否安排补休。"),
        HumanMessage(content="那没有补休呢？"),
    ]}))
    asyncio.run(nodes.classify({"messages": [
        HumanMessage(content="上一条问题"),
        HumanMessage(content="这条问题"),
    ]}))

    assert seen[0]["current_question"] == "那没有补休呢？"
    assert seen[0]["previous_ai"] == "要看是否安排补休。"
    assert seen[1]["previous_ai"] == ""
    assert "previous_turn" not in seen[0]


def test_analyze_case_only_updates_facts_and_preserves_query_history():
    nodes = LegalNodes(model=None)
    case = LegalNodes._new_case("周六加班费怎么算？")
    previous_query = {
        "issue": "休息日加班",
        "title_keywords": ["劳动法"],
        "content_keywords": ["加班工资"],
        "case_facts": "用户周六工作",
    }
    case["retrieval_queries"] = [previous_query]
    case["pending_retrieval_cases"] = [previous_query]
    state = {"active_case_id": "case_1", "cases": {"case_1": case}}

    async def fake_invoke(schema, system, content, *, stream=False):
        assert schema is LegalCaseAnalysisOutput
        assert stream is True
        assert "只输出 objective_facts" in system
        return LegalCaseAnalysisOutput(objective_facts=["用户周六工作"])

    nodes._invoke_json = fake_invoke
    update = asyncio.run(nodes.analyze_case(state))
    updated_case = update["cases"]["case_1"]

    assert updated_case["objective_facts"] == ["用户周六工作"]
    assert updated_case["pending_retrieval_cases"] == []
    assert updated_case["retrieval_queries"] == [previous_query]
