"""LangChain 工具集合。"""

from langchain_core.tools import BaseTool

from ai_agent_learning.langchain_agent.tools.current_time import current_time_tool
from ai_agent_learning.langchain_agent.tools.delete_memory import delete_memory_tool
from ai_agent_learning.langchain_agent.tools.list_memories import list_memories_tool
from ai_agent_learning.langchain_agent.tools.run_command import run_command_tool
from ai_agent_learning.langchain_agent.tools.search_memory import search_memory_tool
from ai_agent_learning.langchain_agent.tools.search_laws import search_laws_tool
from ai_agent_learning.langchain_agent.tools.send_email import send_email_tool
from ai_agent_learning.langchain_agent.tools.update_memory import update_memory_tool
from ai_agent_learning.langchain_agent.tools.web_search import web_search_tool

READ_ONLY_TOOLS: list[BaseTool] = [
    current_time_tool,
    web_search_tool,
    search_memory_tool,
    list_memories_tool,
    search_laws_tool,
]
SIDE_EFFECT_TOOLS: list[BaseTool] = [
    send_email_tool,
    run_command_tool,
    update_memory_tool,
    delete_memory_tool,
]
ALL_TOOLS: list[BaseTool] = [*READ_ONLY_TOOLS, *SIDE_EFFECT_TOOLS]
TOOL_BY_NAME: dict[str, BaseTool] = {tool.name: tool for tool in ALL_TOOLS}

__all__ = [
    "ALL_TOOLS",
    "READ_ONLY_TOOLS",
    "SIDE_EFFECT_TOOLS",
    "TOOL_BY_NAME",
    "current_time_tool",
    "delete_memory_tool",
    "list_memories_tool",
    "run_command_tool",
    "search_memory_tool",
    "search_laws_tool",
    "send_email_tool",
    "update_memory_tool",
    "web_search_tool",
]
