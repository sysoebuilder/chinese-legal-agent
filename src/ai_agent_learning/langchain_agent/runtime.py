"""新版 LangChain Agent 的模型和运行图构建。"""

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from ai_agent_learning.langchain_agent.approval import build_interrupt_on
from ai_agent_learning.langchain_agent.glm import ChatGlm
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
回答中国法律法规问题时，调用 search_raws，并严格遵守该工具说明中的时效与地域规则。
工具没有返回有效结果时，应明确说明无法取得信息，不得编造工具结果。
在调用记忆工具后，不需要说明查询了记忆内容，要表现的跟真人一样。
""".strip()


def create_langchain_model(config: ModelConfig) -> BaseChatModel:
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
        return ChatDeepSeek(
            model=config.model,
            api_key=SecretStr(config.api_key),
            base_url=config.base_url,
            streaming=True,
            stream_usage=False,
            timeout=30.0,
            max_retries=2,
            extra_body={"thinking": {"type": "enabled"}},
        )

    raise ValueError(f"不支持的模型提供商：{config.provider}")


def create_new_chat_agent(
    config: ModelConfig,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    model: BaseChatModel | None = None,
    memory_middleware: MemorySaveMiddleware | None = None,
) -> CompiledStateGraph:
    """创建带上下文总结和高风险工具确认的新版 Agent。"""
    model = model or create_langchain_model(config)
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
            interrupt_on=build_interrupt_on(ALL_TOOLS),
            description_prefix="工具执行需要确认",
        )
    )
    return create_agent(
        model=model,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer or InMemorySaver(),
        name="agent100_newchat",
    )
