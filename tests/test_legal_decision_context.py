import json

from ai_agent_learning.langchain_agent.legal.nodes import LegalNodes


def test_decision_context_includes_past_keywords_without_repeating_case_facts():
    case = LegalNodes._new_case("周六加班费如何计算？")
    case["retrieval_queries"] = [
        {
            "issue": "加班费",
            "title_keywords": ["劳动法"],
            "content_keywords": ["休息日加班"],
            "case_facts": "仅用于检索的案件事实",
        }
    ]
    state = {"active_case_id": "case_1", "cases": {"case_1": case}}

    context = json.loads(LegalNodes._decision_context(state))

    assert context["retrieval_query_history"] == [
        {
            "issue": "加班费",
            "title_keywords": ["劳动法"],
            "content_keywords": ["休息日加班"],
        }
    ]
    assert "仅用于检索的案件事实" not in json.dumps(context, ensure_ascii=False)
