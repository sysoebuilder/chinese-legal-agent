"""停止后继续会话：旧断点不阻塞新任务、不执行旧工具、不丢失历史。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from test_run_api import PUBLIC_MODEL, FakeStore, factory_for

from chinese_legal_agent.api.worker import execute_run


@pytest.mark.parametrize("cancel_at", ["approval", "running", "cleared"])
def test_stop_then_new_run_keeps_history_and_skips_old_pending_node(cancel_at):
    store = FakeStore()
    old = store.create("old", None, PUBLIC_MODEL)
    effects = []

    def prepare(state):
        if state["messages"][-1].content == "old":
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write",
                                "args": {},
                                "id": "unfinished-call",
                            }
                        ],
                    )
                ]
            }
        return {}

    def perform(state):
        if state["messages"][-1].content != "new":
            if cancel_at == "approval":
                interrupt({"action_requests": [{"name": "write", "args": {}}]})
            else:
                store.cancel(old["run_id"])
            effects.append("old-effect")
            return {"messages": [AIMessage(content="old answer")]}
        effects.append("new-answer")
        return {"messages": [AIMessage(content="new answer")]}

    graph = StateGraph(MessagesState)
    graph.add_node("prepare", prepare)
    graph.add_node("perform", perform)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "perform")
    graph.add_edge("perform", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {
        "configurable": {"thread_id": old["thread_id"]},
        "metadata": {"run_id": old["run_id"]},
    }
    if cancel_at == "approval":
        execute_run(store, old["run_id"], 1, factory_for(agent))
        assert store.get(old["run_id"])["status"] == "waiting_approval"
        store.cancel(old["run_id"])
    else:
        # 用静态断点模拟在节点完成后停止，旧下一节点尚未执行。
        paused = graph.compile(
            checkpointer=agent.checkpointer, interrupt_after=["prepare"]
        )
        paused.invoke(
            {"messages": [HumanMessage(id=f"run:{old['run_id']}:user", content="old")]},
            config,
        )
        store.cancel(old["run_id"])
    before = agent.get_state(config)
    assert before.next == ("perform",)
    assert effects == []
    if cancel_at == "cleared":
        # 模拟清理完成、新输入尚未保存时进程退出；重试仍需补齐工具结果。
        agent.update_state(config, None, as_node=END)
        assert not agent.get_state(config).next

    new = store.create("new", old["thread_id"], PUBLIC_MODEL)
    execute_run(store, new["run_id"], 1, factory_for(agent))
    assert store.get(new["run_id"])["status"] == "succeeded"
    assert store.get(new["run_id"])["answer"] == "new answer"
    assert effects == ["new-answer"]
    after = agent.get_state(config)
    assert not after.next and not after.interrupts
    assert [
        message.content
        for message in after.values["messages"]
        if isinstance(message, HumanMessage)
    ] == ["old", "new"]
    tool_result = next(
        message
        for message in after.values["messages"]
        if isinstance(message, ToolMessage)
    )
    assert (
        tool_result.tool_call_id == "unfinished-call" and tool_result.status == "error"
    )
    # 重复投递不重新生成回答。
    execute_run(store, new["run_id"], 1, factory_for(agent))
    assert effects == ["new-answer"]


def test_pending_non_cancelled_or_terminal_checkpoint_is_not_discarded():
    store = FakeStore()
    old = store.create("old", None, PUBLIC_MODEL)
    graph = StateGraph(MessagesState)
    graph.add_node(
        "wait", lambda _: interrupt({"action_requests": [{"name": "write"}]})
    )
    graph.add_edge(START, "wait")
    graph.add_edge("wait", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    execute_run(store, old["run_id"], 1, factory_for(agent))
    config = {"configurable": {"thread_id": old["thread_id"]}}
    before = agent.get_state(config)
    # 注入不同运行，模拟错误队列或终端断点；不能仅凭不属于当前运行就清理。
    unrelated = store.create("new", None, PUBLIC_MODEL)
    store.rows[unrelated["run_id"]]["thread_id"] = old["thread_id"]
    execute_run(store, unrelated["run_id"], 1, factory_for(agent))
    assert store.get(unrelated["run_id"])["status"] == "failed"
    assert agent.get_state(config).config == before.config
    assert agent.get_state(config).interrupts == before.interrupts


def test_cancelled_subgraph_does_not_resume_old_approval_when_retrying_new_run():
    store = FakeStore()
    old = store.create("old", None, PUBLIC_MODEL)
    effects = []

    def answer(state):
        if state["messages"][-1].content == "old":
            interrupt({"action_requests": [{"name": "write", "args": {}}]})
            effects.append("old-tool")
        else:
            effects.append("new-answer")
        return {"messages": [AIMessage(content="new answer")]}

    child = StateGraph(MessagesState)
    child.add_node("answer", answer)
    child.add_edge(START, "answer")
    child.add_edge("answer", END)
    parent = StateGraph(MessagesState)
    parent.add_node("general_agent", child.compile())
    parent.add_edge(START, "general_agent")
    parent.add_edge("general_agent", END)
    agent = parent.compile(checkpointer=InMemorySaver())
    execute_run(store, old["run_id"], 1, factory_for(agent))
    assert store.get(old["run_id"])["status"] == "waiting_approval"
    store.cancel(old["run_id"])
    new = store.create("new", old["thread_id"], PUBLIC_MODEL)
    # 兼容用户已经看到 RuntimeError 后点恢复：新运行此前并未写入 checkpoint。
    store.rows[new["run_id"]]["status"] = "failed"
    store.resume(new["run_id"], {})
    execute_run(store, new["run_id"], 2, factory_for(agent))
    assert store.get(new["run_id"])["status"] == "succeeded"
    assert effects == ["new-answer"]


def test_worker_models_have_separate_clients_closed_in_their_own_event_loop():
    import asyncio

    from chinese_legal_agent.api.runtime import open_worker_model
    from chinese_legal_agent.model_config import ModelConfig

    config = ModelConfig("deepseek", "test", "fake-key", "https://example.com")
    clients = []

    async def task(fail):
        try:
            async with open_worker_model(config) as model:
                clients.append(model.http_async_client)
                assert not model.http_async_client.is_closed
                assert not model.http_client.is_closed
                if fail:
                    raise RuntimeError("simulate cancellation or failure")
        except RuntimeError:
            assert fail
        assert model.http_async_client.is_closed
        assert model.http_client.is_closed

    asyncio.run(task(False))
    asyncio.run(task(True))
    assert clients[0] is not clients[1]
