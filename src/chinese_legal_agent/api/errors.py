"""展开异步异常组，只向客户端和日志提供不含凭据的诊断。"""

import traceback
from pathlib import Path


class WindowsTerminalUnavailable(RuntimeError):
    """显式配置的 Windows 终端 MCP 无法初始化。"""


def exception_leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in exception_leaves(child)]
    return [error]


def public_error(error: BaseException) -> str:
    leaves = exception_leaves(error)
    if any(isinstance(leaf, WindowsTerminalUnavailable) for leaf in leaves):
        return "Windows 终端 MCP 连接失败，请运行 docker compose up 并检查本机服务与连接配置"
    return ", ".join(dict.fromkeys(type(leaf).__name__ for leaf in leaves))


def exception_diagnostics(error: BaseException) -> list[dict]:
    """不记录异常正文、URL、请求头、工具参数或源代码行。"""
    result = []
    for leaf in exception_leaves(error):
        result.append({
            "type": type(leaf).__name__,
            "http_status": getattr(getattr(leaf, "response", None), "status_code", None),
            "frames": [
                f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(leaf.__traceback__)
            ],
        })
    return result
