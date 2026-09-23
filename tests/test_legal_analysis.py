import asyncio

from ai_agent_learning.langchain_agent.legal.nodes import LegalNodes
from ai_agent_learning.langchain_agent.legal.schemas import LegalCaseAnalysisOutput


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

    async def fake_invoke(schema, system, content):
        assert schema is LegalCaseAnalysisOutput
        assert "只输出 objective_facts" in system
        return LegalCaseAnalysisOutput(objective_facts=["用户周六工作"])

    nodes._invoke_json = fake_invoke
    update = asyncio.run(nodes.analyze_case(state))
    updated_case = update["cases"]["case_1"]

    assert updated_case["objective_facts"] == ["用户周六工作"]
    assert updated_case["pending_retrieval_cases"] == []
    assert updated_case["retrieval_queries"] == [previous_query]
