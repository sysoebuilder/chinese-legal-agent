import asyncio
from contextlib import asynccontextmanager
from io import StringIO
from types import SimpleNamespace

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr
from rich.console import Console

from ai_agent_learning.langchain_agent import chat as chat_module
from ai_agent_learning.langchain_agent import runtime
from ai_agent_learning.langchain_agent import run as run_module
from ai_agent_learning.langchain_agent.chat import (
    _new_turn_config,
    _resume_pending_turn,
    stream_agent_turn,
)
from ai_agent_learning.model_config import ModelConfig
from ai_agent_learning.ui.terminal import TerminalUI


def test_new_chat_exit_before_first_message_does_not_create_session(monkeypatch, tmp_path):
    created = []
    closed = []
    mcp_tools = [object()]
    postgres_connection = object()
    postgres_checkpointer = SimpleNamespace(conn=postgres_connection)
    setup = []

    class FakeSessionStore:
        def __init__(self, connection):
            assert connection is postgres_connection

        async def list_sessions(self):
            return []

        async def create_session(self):
            created.append(True)
            raise AssertionError("退出前不应创建会话")

    class FakeUI:
        def show_sessions(self, sessions):
            assert sessions == []

        def show_header(self, model, session_name):
            assert session_name == ""

        def read_message(self):
            return "EXIT"

    class FakeServer:
        async def start(self):
            pass

        async def close(self):
            closed.append("qdrant")

    class FakeWorker:
        is_idle = True

        def __init__(self, *args):
            pass

        def start(self):
            pass

        async def close(self):
            closed.append("memory")

    @asynccontextmanager
    async def fake_session_store(url):
        try:
            assert url == "postgresql://localhost/test"
            yield FakeSessionStore(postgres_connection)
        finally:
            closed.append("sessions")

    @asynccontextmanager
    async def fake_checkpointer(url):
        assert url == "postgresql://localhost/test"
        setup.append("postgres")
        try:
            yield postgres_checkpointer
        finally:
            closed.append("postgres")

    @asynccontextmanager
    async def fake_connect_servers(**kwargs):
        assert kwargs["cloud_servers"] == {"remote": {"url": "https://remote.example/mcp"}}
        try:
            yield SimpleNamespace(tools=mcp_tools, errors={})
        finally:
            closed.append("mcp")

    def fake_create_agent(*args, **kwargs):
        assert kwargs["mcp_tools"] is mcp_tools
        assert kwargs["checkpointer"] is postgres_checkpointer
        return object()

    monkeypatch.setattr("builtins.input", lambda _: "0")
    monkeypatch.setattr(run_module, "get_database_url", lambda: "postgresql://localhost/test")
    monkeypatch.setattr(run_module, "migrate_database", lambda _: None)
    monkeypatch.setattr(run_module, "configure_logging", lambda _: None)
    monkeypatch.setattr(run_module, "shutdown_logging", lambda: None)
    monkeypatch.setattr(run_module, "load_active_model", lambda: SimpleNamespace(model="test"))
    monkeypatch.setattr(run_module, "QdrantServer", FakeServer)
    monkeypatch.setattr(run_module, "TerminalUI", FakeUI)
    monkeypatch.setattr(run_module, "MemoryWorker", FakeWorker)
    monkeypatch.setattr(run_module, "create_langchain_model", lambda _: object())
    monkeypatch.setattr(run_module, "create_new_chat_agent", fake_create_agent)
    monkeypatch.setattr(run_module, "connect_servers", fake_connect_servers)
    monkeypatch.setattr(run_module, "CLOUD_MCP_SERVERS", {"remote": {"url": "https://remote.example/mcp"}})
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(run_module, "open_session_store", fake_session_store)
    monkeypatch.setattr(run_module, "open_checkpointer", fake_checkpointer)

    asyncio.run(chat_module.run_new_chat())

    assert created == []
    assert setup == ["postgres"]
    assert closed == ["memory", "mcp", "postgres", "sessions", "qdrant"]


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


def test_stream_agent_turn_shows_structured_reasoning_without_json():
    final_messages = [HumanMessage(content="加班费怎么算？"), AIMessage(content="请补充工资。")]

    class FakeAgent:
        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content=[{"type": "reasoning", "reasoning": "先核对工资。"}]),
                    {"langgraph_node": "classify", "tags": ["legal_structured"]},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content='{"action":"ask_user"}'),
                    {"langgraph_node": "classify", "tags": ["legal_structured"]},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(content="请补充工资。"),
                    {"langgraph_node": "generate_final_answer"},
                ),
            }
            yield {
                "type": "values",
                "data": {"messages": final_messages},
                "interrupts": (),
            }

    class RecordingUI(TerminalUI):
        def __init__(self):
            super().__init__(Console(file=StringIO(), force_terminal=False))
            self.reasoning_parts = []
            self.text_parts = []

        def reasoning_delta(self, text):
            self.reasoning_parts.append(text)

        def text_delta(self, text):
            self.text_parts.append(text)

    ui = RecordingUI()
    result = asyncio.run(
        stream_agent_turn(
            FakeAgent(),
            "加班费怎么算？",
            ui,
            RunnableConfig(configurable={"thread_id": "test-thread"}),
        )
    )

    assert result == final_messages
    assert ui.reasoning_parts == ["先核对工资。"]
    assert ui.text_parts == ["请补充工资。"]


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


def test_resume_pending_turn_can_be_declined(monkeypatch):
    class FakeAgent:
        async def aget_state(self, config):
            return SimpleNamespace(next=("general_agent",))

        async def astream(self, input, *, config, stream_mode, subgraphs, version):
            raise AssertionError("用户跳过恢复后不应执行断点")
            yield

    ui = TerminalUI(Console(file=StringIO(), force_terminal=False))
    monkeypatch.setattr(ui, "confirm_resume", lambda: False)
    assert asyncio.run(_resume_pending_turn(FakeAgent(), ui, "test-thread", confirm=True)) is None


def test_decide_fault_config_is_used_once(monkeypatch):
    monkeypatch.setenv("LEGAL_FAULT_DECIDE_ONCE", "1")

    first = _new_turn_config("test-thread", "trace-1")
    second = _new_turn_config("test-thread", "trace-2")

    assert first["configurable"]["legal_fault"] == {
        "node": "decide",
        "mode": "error",
    }
    assert second["configurable"] == {"thread_id": "test-thread"}
