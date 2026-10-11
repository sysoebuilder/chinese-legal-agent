"""法律 Agent 子图的节点和边。"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from chinese_legal_agent.langchain_agent.legal.nodes import LegalNodes
from chinese_legal_agent.langchain_agent.legal.state import LegalAgentState, LegalCase

logger = logging.getLogger(__name__)


def _timed_node(
    name: str,
    node: Callable[[LegalAgentState], Awaitable[dict[str, Any]]],
) -> Any:
    """记录法律节点耗时，并保留原有返回值与异常行为。"""

    async def wrapped(state: LegalAgentState) -> dict[str, Any]:
        config = get_config()
        fields = {
            "node_name": name,
            "session_id": (config.get("configurable") or {}).get("thread_id"),
            "trace_id": (config.get("metadata") or {}).get("trace_id"),
        }
        started_at = time.perf_counter()
        try:
            fault = (config.get("configurable") or {}).get("legal_fault")
            if (
                isinstance(fault, dict)
                and fault.get("node") == name
                and fault.get("mode") == "error"
            ):
                raise RuntimeError(f"法律节点测试故障：{name}")
            result = await node(state)
        except GraphBubbleUp:
            # interrupt 是正常暂停信号，交还给图运行时处理。
            raise
        except Exception as error:
            logger.warning(
                "法律节点执行失败",
                extra={
                    "event": "legal.node.failed",
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "error_type": type(error).__name__,
                    **fields,
                },
            )
            raise
        logger.info(
            "法律节点执行完成",
            extra={
                "event": "legal.node.completed",
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                **fields,
            },
        )
        return result

    return wrapped


def create_legal_graph(
    model: BaseChatModel,
    *,
    law_tool: BaseTool | None = None,
) -> CompiledStateGraph:
    """使用当前对话模型构建独立法律子图。"""
    nodes = LegalNodes(model, law_tool) if law_tool is not None else LegalNodes(model)
    graph = StateGraph(LegalAgentState)

    graph.add_node("initialize", _timed_node("initialize", nodes.initialize))
    graph.add_node("classify", _timed_node("classify", nodes.classify))
    graph.add_node("analyze_case", _timed_node("analyze_case", nodes.analyze_case))
    graph.add_node("retrieve_laws", _timed_node("retrieve_laws", nodes.retrieve_laws))
    graph.add_node("decide", _timed_node("decide", nodes.decide))
    graph.add_node("ask_user", _timed_node("ask_user", nodes.ask_user))
    graph.add_node(
        "generate_final_answer",
        _timed_node("generate_final_answer", nodes.generate_final_answer),
    )
    graph.add_node("refuse", _timed_node("refuse", nodes.refuse))

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "classify")
    graph.add_conditional_edges(
        "classify",
        _route_classification,
        {"legal": "analyze_case", "general": END, "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "analyze_case",
        _route_analysis,
        {"decide": "decide", "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "retrieve_laws",
        _route_retrieval,
        {"decide": "decide", "retry": "retrieve_laws", "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "decide",
        _route_decision,
        {
            "retry": "decide",
            "ask_user": "ask_user",
            "retrieve_more": "retrieve_laws",
            "final_answer": "generate_final_answer",
            "refuse": "refuse",
        },
    )
    graph.add_conditional_edges(
        "ask_user",
        _route_after_user,
        {"analyze_case": "analyze_case", "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "generate_final_answer",
        _route_final_answer,
        {
            "end": END,
            "retry": "generate_final_answer",
            "ask_user": "ask_user",
            "retrieve_more": "retrieve_laws",
            "refuse": "refuse",
        },
    )
    graph.add_edge("refuse", END)
    return graph.compile()


def _route_classification(
    state: LegalAgentState,
) -> Literal["legal", "general", "refuse"]:
    if state.get("active_case_id") is None:
        return "general"
    if _active_case(state).get("refusal_reason"):
        return "refuse"
    return "legal"


def _route_retrieval(state: LegalAgentState) -> Literal["decide", "retry", "refuse"]:
    case = _active_case(state)
    if case.get("refusal_reason"):
        return "refuse"
    return "retry" if case.get("retry_target") == "retrieve_laws" else "decide"


def _route_analysis(state: LegalAgentState) -> Literal["decide", "refuse"]:
    return "refuse" if _active_case(state).get("refusal_reason") else "decide"


def _route_decision(state: LegalAgentState) -> str:
    case = _active_case(state)
    if case.get("refusal_reason"):
        return "refuse"
    if case.get("retry_target") == "decide":
        return "retry"
    decision = case.get("decision")
    return str(decision.get("action") or "refuse") if decision else "refuse"


def _route_after_user(state: LegalAgentState) -> Literal["analyze_case", "refuse"]:
    return "refuse" if _active_case(state).get("refusal_reason") else "analyze_case"


def _route_final_answer(
    state: LegalAgentState,
) -> Literal["end", "retry", "ask_user", "retrieve_more", "refuse"]:
    case = _active_case(state)
    if case.get("refusal_reason"):
        return "refuse"
    if case.get("retry_target") == "generate_final_answer":
        return "retry"
    if case.get("final_answer"):
        return "end"
    decision = case.get("decision")
    action = decision.get("action") if decision else None
    if action == "ask_user":
        return "ask_user"
    if action == "retrieve_more":
        return "retrieve_more"
    return "refuse"


def _active_case(state: LegalAgentState) -> LegalCase:
    case_id = state.get("active_case_id")
    if not case_id:
        raise ValueError("法律流程没有活动案件")
    try:
        return state.get("cases", {})[case_id]
    except KeyError:
        raise ValueError("活动案件不存在") from None
