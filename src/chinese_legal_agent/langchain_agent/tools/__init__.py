"""LangChain 工具集合。"""

import os

_IS_WINDOWS = os.name == "nt"


def _load_registry() -> None:
    # 导入单个工具时不初始化整个注册表，避免终端 MCP 加载记忆和法律检索依赖。
    from chinese_legal_agent.langchain_agent.tools.current_time import current_time_tool
    from chinese_legal_agent.langchain_agent.tools.delete_memory import delete_memory_tool
    from chinese_legal_agent.langchain_agent.tools.list_memories import list_memories_tool
    from chinese_legal_agent.langchain_agent.tools.run_command import run_command_tool
    from chinese_legal_agent.langchain_agent.tools.search_laws import search_laws_tool
    from chinese_legal_agent.langchain_agent.tools.search_memory import search_memory_tool
    from chinese_legal_agent.langchain_agent.tools.send_email import send_email_tool
    from chinese_legal_agent.langchain_agent.tools.update_memory import update_memory_tool
    from chinese_legal_agent.langchain_agent.tools.web_search import web_search_tool

    read_only_tools = [current_time_tool, web_search_tool, search_memory_tool, list_memories_tool, search_laws_tool]
    side_effect_tools = [send_email_tool, run_command_tool, update_memory_tool, delete_memory_tool]
    if not _IS_WINDOWS:
        side_effect_tools.remove(run_command_tool)
    all_tools = [*read_only_tools, *side_effect_tools]
    exported_tools = [*read_only_tools, send_email_tool, run_command_tool, update_memory_tool, delete_memory_tool]
    globals().update({tool.name + "_tool": tool for tool in exported_tools})
    globals().update(
        READ_ONLY_TOOLS=read_only_tools,
        SIDE_EFFECT_TOOLS=side_effect_tools,
        ALL_TOOLS=all_tools,
        TOOL_BY_NAME={tool.name: tool for tool in all_tools},
    )


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    _load_registry()
    return globals()[name]

__all__ = [
    "ALL_TOOLS",
    "READ_ONLY_TOOLS",
    "SIDE_EFFECT_TOOLS",
    "TOOL_BY_NAME",
    "current_time_tool",
    "delete_memory_tool",
    "list_memories_tool",
    "run_command_tool",
    "search_laws_tool",
    "search_memory_tool",
    "send_email_tool",
    "update_memory_tool",
    "web_search_tool",
]
