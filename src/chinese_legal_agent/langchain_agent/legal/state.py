"""法律 Agent 子图使用的状态类型。"""

from typing import Any, Literal, Required, TypedDict

from langchain.agents.middleware import AgentState

LegalAction = Literal[
    "ask_user",
    "retrieve_more",
    "final_answer",
    "uncertain_answer",
    "refuse",
]


class LegalRetrievalCase(TypedDict):
    """一次法律检索所需的单个争议点。"""

    issue: str
    title_keywords: list[str]
    content_keywords: list[str]
    case_facts: str


class RetrievedLaw(TypedDict):
    """法律工具返回并附带所属争议点的单条候选法律。"""

    issue: str
    id: str
    payload: dict[str, Any] | None
    bm25_score: float | None
    vector_score: float | None


class LegalDecision(TypedDict, total=False):
    """外部决策模型写回状态的结构化决定。"""

    action: Required[LegalAction]
    reason: Required[str]
    question: str
    retrieval_cases: list[LegalRetrievalCase]
    usable_law_ids: list[str]
    uncertainty_points: list[str]


class LegalCase(TypedDict):
    """按案件隔离保存的事实、检索结果和最近回答。"""

    current_question: str
    original_question: str
    user_supplements: list[str]
    asked_questions: list[str]
    objective_facts: list[str]
    uncertainty_points: list[str]
    retrieval_round: int
    pending_retrieval_cases: list[LegalRetrievalCase]
    retrieval_feedback: str | None
    invalid_retrieval_count: int
    retry_target: Literal[
        "retrieve_laws", "decide", "generate_final_answer", "generate_uncertain_answer"
    ] | None
    retry_count: int
    last_node_error: str | None
    retrieval_queries: list[LegalRetrievalCase]
    retrieved_laws: list[RetrievedLaw]
    usable_laws: list[RetrievedLaw]
    decision: LegalDecision | None
    final_answer: str | None
    refusal_reason: str | None


class LegalAgentState(AgentState[Any]):
    """法律子图的完整共享状态。"""

    cases: dict[str, LegalCase]
    active_case_id: str | None
