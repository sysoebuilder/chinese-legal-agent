"""LangChain 工具集合。"""

from langchain_core.tools import BaseTool

from ai_agent_learning.langchain_agent.tools.current_time import current_time_tool
from ai_agent_learning.langchain_agent.tools.run_command import run_command_tool
from ai_agent_learning.langchain_agent.tools.search_memory import search_memory_tool
from ai_agent_learning.langchain_agent.tools.search_raws import search_raws_tool
from ai_agent_learning.langchain_agent.tools.send_email import send_email_tool
from ai_agent_learning.langchain_agent.tools.web_search import web_search_tool
from ai_agent_learning.tools.builtin.current_time import SPEC as current_time_spec
from ai_agent_learning.tools.builtin.run_command import SPEC as run_command_spec
from ai_agent_learning.tools.builtin.search_memory import SPEC as search_memory_spec
from ai_agent_learning.tools.builtin.search_raws import SPEC as search_raws_spec
from ai_agent_learning.tools.builtin.send_email import SPEC as send_email_spec
from ai_agent_learning.tools.builtin.web_search import SPEC as web_search_spec

READ_ONLY_TOOLS: list[BaseTool] = [
    current_time_tool,
    web_search_tool,
    search_memory_tool,
    search_raws_tool,
]
SIDE_EFFECT_TOOLS: list[BaseTool] = [send_email_tool, run_command_tool]
ALL_TOOLS: list[BaseTool] = [*READ_ONLY_TOOLS, *SIDE_EFFECT_TOOLS]
TOOL_SPECS = {
    spec.name: spec
    for spec in (
        current_time_spec,
        web_search_spec,
        search_memory_spec,
        search_raws_spec,
        send_email_spec,
        run_command_spec,
    )
}

__all__ = [
    "ALL_TOOLS",
    "READ_ONLY_TOOLS",
    "SIDE_EFFECT_TOOLS",
    "TOOL_SPECS",
    "current_time_tool",
    "run_command_tool",
    "search_memory_tool",
    "search_raws_tool",
    "send_email_tool",
    "web_search_tool",
]
