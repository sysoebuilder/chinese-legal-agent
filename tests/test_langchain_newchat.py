import asyncio
from io import StringIO
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from pydantic import SecretStr
from rich.console import Console

from ai_agent_learning.langchain_agent import runtime
from ai_agent_learning.langchain_agent.chat import (
    _new_turn_config,
    _resume_pending_turn,
    stream_agent_turn,
)
from ai_agent_learning.model_config import ModelConfig
from ai_agent_learning.ui.terminal import TerminalUI


def test_create_new_chat_agent_uses_glm_adapter_and_registers_query_tools(monkeypatch):
    captured = {}

    class FakeModel:
        _llm_type = "fake-chat"

        def with_retry(self):
            return self

    fake_model = FakeModel()
    fake_agent = RunnableLambda(lambda state: state)

    def fake_chat_glm(**kwargs):
        captured["model_options"] = kwargs
        return fake_model

    def fake_create_agent(**kwargs):
        captured["agent_options"] = kwargs
        return fake_agent

    monkeypatch.setattr(runtime, "ChatGlm", fake_chat_glm)
    monkeypatch.setattr(runtime, "create_agent", fake_create_agent)
    config = ModelConfig(
        provider="glm",
        model="test-model",
        api_key="test-key",
        base_url="https://model.test/v1/",
    )

    result = runtime.create_new_chat_agent(config)

    assert result.name == "agent100_newchat"
    assert captured["model_options"] == {
        "model": "test-model",
        "api_key": "test-key",
        "api_base": "https://model.test/v1/chat/completions",
        "streaming": True,
        "thinking": "enabled",
    }
    assert captured["agent_options"]["model"] is fake_model
    assert [tool.name for tool in captured["agent_options"]["tools"]] == [
        "current_time",
        "web_search",
        "search_memory",
        "send_email",
        "run_command",
    ]
    middleware = captured["agent_options"]["middleware"]
    assert len(middleware) == 4
    assert middleware[0].trigger == ("tokens", 102400)
    assert middleware[0].keep == ("messages", 6)
    assert middleware[1].max_retries == 2
    assert middleware[1].retry_on is runtime.is_retryable_error
    assert set(middleware[3].interrupt_on) == {"send_email"}


def test_create_langchain_model_uses_deepseek_adapter(monkeypatch):
    captured = {}
    fake_model = object()

    def fake_chat_deepseek(**kwargs):
        captured.update(kwargs)
        return fake_model

    monkeypatch.setattr(runtime, "ChatDeepSeek", fake_chat_deepseek)
    config = ModelConfig(
        provider="deepseek",
        model="deepseek-test",
        api_key="deepseek-key",
        base_url="https://deepseek.test/",
    )

    result = runtime.create_langchain_model(config)

    assert result is fake_model
    assert captured == {
        "model": "deepseek-test",
        "api_key": SecretStr("deepseek-key"),
        "base_url": "https://deepseek.test/",
        "streaming": True,
        "stream_usage": False,
        "timeout": 30.0,
        "max_retries": 2,
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def test_stream_agent_turn_outputs_deltas_and_returns_complete_state():
    previous = [HumanMessage(content="上一轮问题"), AIMessage(content="上一轮回答")]
    final_messages = [
        *previous,
        HumanMessage(content="现在几点"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "current_time",
                    "args": {},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content='{"time":"14:30:00"}',
            tool_call_id="call-1",
            name="current_time",
        ),
        AIMessage(content="现在是14:30。"),
    ]

    class FakeAgent:
        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            assert input["messages"][0].content == "现在几点"
            assert config["configurable"]["thread_id"] == "test-thread"
            assert stream_mode == ["messages", "values"]
            assert subgraphs is True
            assert version == "v2"
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content=""),
                    {"langgraph_node": "model"},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    ToolMessage(
                        content='{"time":"14:30:00"}',
                        tool_call_id="call-1",
                        name="current_time",
                    ),
                    {"langgraph_node": "tools"},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content="现在是"),
                    {"langgraph_node": "model"},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content="14:30。"),
                    {"langgraph_node": "model"},
                ),
            }
            yield {
                "type": "values",
                "data": {"messages": final_messages},
                "interrupts": (),
            }

    output = StringIO()
    ui = TerminalUI(Console(file=output, force_terminal=False))

    result = asyncio.run(
        stream_agent_turn(
            FakeAgent(),
            "现在几点",
            ui,
            RunnableConfig(configurable={"thread_id": "test-thread"}),
        )
    )

    assert result == final_messages
    assert output.getvalue() == "助手: 现在是14:30。\n"


def test_stream_agent_turn_rejects_missing_final_state():
    class EmptyAgent:
        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            if False:
                yield SimpleNamespace()

    output = StringIO()
    ui = TerminalUI(Console(file=output, force_terminal=False))

    try:
        asyncio.run(
            stream_agent_turn(
                EmptyAgent(),
                "你好",
                ui,
                RunnableConfig(configurable={"thread_id": "test-thread"}),
            )
        )
    except RuntimeError as error:
        assert str(error) == "LangChain Agent 没有返回最终状态"
    else:
        raise AssertionError("缺少最终状态时应抛出 RuntimeError")


def test_resume_pending_turn_uses_checkpoint_without_new_user_message():
    final_messages = [HumanMessage(content="原问题"), AIMessage(content="恢复后的回答")]

    class FakeAgent:
        async def aget_state(self, config):
            assert config["configurable"]["thread_id"] == "test-thread"
            return SimpleNamespace(next=("legal_flow",))

        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            assert input is None
            assert config["configurable"]["thread_id"] == "test-thread"
            yield {
                "type": "values",
                "data": {"messages": final_messages},
                "interrupts": (),
            }

    output = StringIO()
    ui = TerminalUI(Console(file=output, force_terminal=False))
    result = asyncio.run(_resume_pending_turn(FakeAgent(), ui, "test-thread"))

    assert result == final_messages


def test_resume_pending_turn_skips_completed_checkpoint():
    class FakeAgent:
        async def aget_state(self, config):
            return SimpleNamespace(next=())

        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            raise AssertionError("已完成的会话不应再次执行")
            yield

    ui = TerminalUI(Console(file=StringIO(), force_terminal=False))
    assert asyncio.run(_resume_pending_turn(FakeAgent(), ui, "test-thread")) is None


def test_decide_fault_config_is_used_once(monkeypatch):
    monkeypatch.setenv("LEGAL_FAULT_DECIDE_ONCE", "1")

    first = _new_turn_config("test-thread", "trace-1")
    second = _new_turn_config("test-thread", "trace-2")

    assert first["configurable"]["legal_fault"] == {
        "node": "decide",
        "mode": "error",
    }
    assert second["configurable"] == {"thread_id": "test-thread"}
