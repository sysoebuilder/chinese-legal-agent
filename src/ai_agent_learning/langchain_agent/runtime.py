"""新版 LangChain Agent 的模型和运行图构建。"""

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from ai_agent_learning.config import APPConfig
from ai_agent_learning.langchain_agent.approval import build_interrupt_on
from ai_agent_learning.langchain_agent.deepseek import (
    ChatDeepSeekWithJsonReasoning as ChatDeepSeek,
)
from ai_agent_learning.langchain_agent.glm import ChatGlm
from ai_agent_learning.langchain_agent.legal import (
    LegalAgentState,
    create_legal_graph,
)
from ai_agent_learning.langchain_agent.middleware import (
    ToolLoggingMiddleware,
    build_context_middleware,
)
from ai_agent_learning.langchain_agent.middleware.memory import MemorySaveMiddleware
from ai_agent_learning.langchain_agent.tools import ALL_TOOLS
from ai_agent_learning.model_config import ModelConfig
from ai_agent_learning.retry import is_retryable_error

SYSTEM_PROMPT = """
你是一个通用型个人助手。
需要知道当前日期、时间或时区时，必须调用 current_time 工具。
问题涉及实时信息或用户明确要求联网时，调用 web_search。
需要依赖用户过去提供的信息时，调用 search_memory，不得假装记得未检索到的内容。
工具没有返回有效结果时，应明确说明无法取得信息，不得编造工具结果。
在调用记忆工具后，不需要说明查询了记忆内容，要表现的跟真人一样。
""".strip()


def create_langchain_model(
    config: ModelConfig, *, http_client: Any = None, http_async_client: Any = None,
) -> BaseChatModel:
    """根据当前提供商创建对应的 LangChain 模型适配器。"""
    if config.provider == "glm":
        api_base = config.base_url.rstrip("/")
        if not api_base.endswith("/chat/completions"):
            api_base += "/chat/completions"
        return ChatGlm(
            model=config.model,
            api_key=config.api_key,
            api_base=api_base,
            streaming=True,
            thinking="enabled",
        )

    if config.provider == "deepseek":
        # Pydantic 在运行时校验继承字段；使用配置字典避免编辑器误判构造签名。
        options: dict[str, Any] = {
            "model": config.model,
            "api_key": SecretStr(config.api_key),
            "base_url": config.base_url,
            "streaming": True,
            "stream_usage": False,
            "timeout": 30.0,
            "max_retries": 2,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
        if http_client is not None:
            options["http_client"] = http_client
        if http_async_client is not None:
            options["http_async_client"] = http_async_client
        return ChatDeepSeek(**options)

    raise ValueError(f"不支持的模型提供商：{config.provider}")


def create_new_chat_agent(
    config: ModelConfig | APPConfig,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    model: BaseChatModel | None = None,
    memory_middleware: MemorySaveMiddleware | None = None,
    mcp_tools: Sequence[BaseTool] | None = None,
    app_config: APPConfig | None = None,
) -> CompiledStateGraph:
    """根据应用配置过滤工具，并在主图入口判断是否启用法律子图。"""
    if isinstance(config, APPConfig):
        app_config = config
        config = config.active_model()
    app_config = app_config or APPConfig()
    model = model or create_langchain_model(config)
    general_tools: list[BaseTool] = [
        tool for tool in ALL_TOOLS
        if tool.name != "search_laws" and app_config.mouldesconfig.tool_enabled(tool.name)
    ]
    registered_names = {tool.name for tool in general_tools}
    for tool in mcp_tools or []:
        if not app_config.mouldesconfig.tool_enabled(tool.name):
            continue
        if tool.name in registered_names:
            raise ValueError(f"重复的工具名称：{tool.name}")
        general_tools.append(tool)
        registered_names.add(tool.name)
    middleware: list[AgentMiddleware] = [build_context_middleware(model)]
    if memory_middleware is not None:
        middleware.append(memory_middleware)
    middleware.append(
        ToolRetryMiddleware(
            max_retries=2,
            retry_on=is_retryable_error,
        )
    )
    # 放在重试中间件内层，使每次实际尝试都产生独立日志。
    middleware.append(ToolLoggingMiddleware())
    middleware.append(
        HumanInTheLoopMiddleware(
            interrupt_on=build_interrupt_on(general_tools, app_config.permissionsconfig),
            description_prefix="工具执行需要确认",
        )
    )
    # 避免提示词要求模型调用已被用户关闭的工具。
    prompt = "\n".join(
        line for line in SYSTEM_PROMPT.splitlines()
        if not any(name in line and name not in registered_names for name in ("current_time", "web_search", "search_memory"))
    )
    general_agent = create_agent(
        model=model,
        tools=general_tools,
        system_prompt=prompt,
        middleware=middleware,
        state_schema=LegalAgentState,
        name="general_agent",
    )
    legal_graph = create_legal_graph(model)
    graph = StateGraph(LegalAgentState)
    graph.add_node("legal_flow", legal_graph)
    graph.add_node("general_agent", general_agent)

    async def queue_legal_memory(state: LegalAgentState) -> dict[str, object]:
        case_id = state.get("active_case_id")
        case = state.get("cases", {}).get(case_id, {}) if case_id else {}
        final_answer = case.get("final_answer")
        if memory_middleware is None or not final_answer:
            return {}
        user_parts = [case["original_question"], *case.get("user_supplements", [])]
        memory_middleware.enqueue("\n".join(user_parts), final_answer)
        return {}

    graph.add_node("queue_legal_memory", queue_legal_memory)

    def check_legal_enabled(state: LegalAgentState) -> dict[str, object]:
        return {} if app_config.mouldesconfig.legal.able else {"active_case_id": None}

    graph.add_node("check_legal_enabled", check_legal_enabled)
    graph.add_edge(START, "check_legal_enabled")
    graph.add_conditional_edges(
        "check_legal_enabled",
        lambda state: "legal_flow" if app_config.mouldesconfig.legal.able else "general_agent",
        {"legal_flow": "legal_flow", "general_agent": "general_agent"},
    )
    graph.add_conditional_edges(
        "legal_flow",
        _route_after_legal_flow,
        {
            "general_agent": "general_agent",
            "queue_legal_memory": "queue_legal_memory",
            "end": END,
        },
    )
    graph.add_edge("general_agent", END)
    graph.add_edge("queue_legal_memory", END)
    return graph.compile(
        checkpointer=checkpointer or InMemorySaver(),
        name="agent100_newchat",
    )


def _route_after_legal_flow(
    state: LegalAgentState,
) -> str:
    case_id = state.get("active_case_id")
    if case_id is None:
        return "general_agent"
    case = state.get("cases", {}).get(case_id, {})
    if case.get("final_answer"):
        return "queue_legal_memory"
    return "end"
