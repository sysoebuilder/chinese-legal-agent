import asyncio
import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph

from chinese_legal_agent.langchain_agent.legal import graph as legal_graph
from chinese_legal_agent.langchain_agent.legal.state import LegalAgentState
from chinese_legal_agent.logging_config import _record_payload


def test_timed_node_records_success_without_changing_result(monkeypatch, caplog):
    monkeypatch.setattr(
        legal_graph,
        "get_config",
        lambda: {
            "configurable": {"thread_id": "thread-1"},
            "metadata": {"trace_id": "trace-1"},
        },
    )

    async def node(state):
        return {"cases": state["cases"]}

    with caplog.at_level(logging.INFO):
        result = asyncio.run(legal_graph._timed_node("classify", node)({"cases": {}}))

    assert result == {"cases": {}}
    record = next(r for r in caplog.records if r.event == "legal.node.completed")
    assert record.node_name == "classify"
    assert record.session_id == "thread-1"
    assert record.trace_id == "trace-1"
    assert record.duration_ms >= 0
    assert _record_payload(record)["node_name"] == "classify"


def test_timed_node_records_failure_and_reraises(monkeypatch, caplog):
    monkeypatch.setattr(legal_graph, "get_config", dict)

    async def node(state):
        raise ValueError("test failure")

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="test failure"):
        asyncio.run(legal_graph._timed_node("retrieve_laws", node)({}))

    record = next(r for r in caplog.records if r.event == "legal.node.failed")
    assert record.node_name == "retrieve_laws"
    assert record.error_type == "ValueError"
    assert record.duration_ms >= 0


def test_timed_node_does_not_log_interrupt_as_failure(monkeypatch, caplog):
    monkeypatch.setattr(legal_graph, "get_config", dict)

    async def node(state):
        raise GraphInterrupt([])

    with caplog.at_level(logging.INFO), pytest.raises(GraphInterrupt):
        asyncio.run(legal_graph._timed_node("ask_user", node)({}))

    assert not any(record.name == legal_graph.__name__ for record in caplog.records)


def test_timed_node_injects_decide_error_before_running_node(monkeypatch, caplog):
    monkeypatch.setattr(
        legal_graph,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "legal_fault": {"node": "decide", "mode": "error"},
            }
        },
    )
    called = False

    async def node(state):
        nonlocal called
        called = True
        return {}

    with caplog.at_level(logging.WARNING), pytest.raises(
        RuntimeError, match="法律节点测试故障：decide"
    ):
        asyncio.run(legal_graph._timed_node("decide", node)({}))

    assert called is False
    record = next(r for r in caplog.records if r.event == "legal.node.failed")
    assert record.node_name == "decide"
    assert record.error_type == "RuntimeError"


def test_decide_fault_resumes_from_checkpoint_without_repeating_prior_node():
    calls = {"before": 0, "decide": 0}

    async def before(state):
        calls["before"] += 1
        return {"cases": {"count": state["cases"]["count"] + 1}}

    async def decide(state):
        calls["decide"] += 1
        return {"cases": {"count": state["cases"]["count"] + 1}}

    builder = StateGraph(LegalAgentState)
    builder.add_node("before", legal_graph._timed_node("before", before))
    builder.add_node("decide", legal_graph._timed_node("decide", decide))
    builder.add_edge(START, "before")
    builder.add_edge("before", "decide")
    builder.add_edge("decide", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    async def run():
        fault_config = {
            "configurable": {
                "thread_id": "fault-test",
                "legal_fault": {"node": "decide", "mode": "error"},
            }
        }
        with pytest.raises(RuntimeError, match="法律节点测试故障：decide"):
            await graph.ainvoke({"messages": [], "cases": {"count": 0}}, config=fault_config)

        resume_config = {"configurable": {"thread_id": "fault-test"}}
        snapshot = await graph.aget_state(resume_config)
        assert snapshot.next == ("decide",)
        return await graph.ainvoke(None, config=resume_config)

    result = asyncio.run(run())

    assert result["cases"]["count"] == 2
    assert calls == {"before": 1, "decide": 1}
