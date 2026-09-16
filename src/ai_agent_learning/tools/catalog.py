from ai_agent_learning.tools.builtin import (
    current_time,
    run_command,
    search_memory,
    search_raws,
    send_email,
    web_search,
)
from ai_agent_learning.tools.registry import RegisteredTool


def build_builtin_tools() -> tuple[RegisteredTool, ...]:
    """统一注册内置工具，外部连接由各工具自行管理。"""
    return (
        RegisteredTool(spec=current_time.SPEC, handler=current_time.handler),
        RegisteredTool(spec=send_email.SPEC, handler=send_email.handler),
        RegisteredTool(spec=web_search.SPEC, handler=web_search.handler),
        RegisteredTool(spec=run_command.SPEC, handler=run_command.handler),
        RegisteredTool(spec=search_memory.SPEC, handler=search_memory.handler),
        RegisteredTool(spec=search_raws.SPEC, handler=search_raws.handler),
    )
