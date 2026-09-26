"""current_time 工具实现与 LangChain 注册。"""

from datetime import datetime

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel

TOOL_DESCRIPTION = (
    "获取运行程序所在系统的当前本地时间，返回包含时区信息的 ISO 8601 时间。"
)
TOOL_METADATA = {
    "risk_level": RiskLevel.LOW.value,
    "has_side_effects": False,
    "supports_idempotent_retry": False,
    "manages_own_timeout": False,
}


async def handler() -> dict[str, str]:
    try:
        now = datetime.now().astimezone()
        raw_offset = now.strftime("%z")
    except (OSError, OverflowError, ValueError) as error:
        print(f"Error in current_time: {error}")
        return {
            "error": str(error),
        }

    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "timezone": now.tzname() or "",
        "utc_offset": (f"{raw_offset[:3]}:{raw_offset[3:]}"),
    }


class CurrentTimeInput(BaseModel):
    """当前时间工具没有输入参数。"""

    model_config = ConfigDict(extra="forbid")


current_time_tool = StructuredTool.from_function(
    coroutine=handler,
    name="current_time",
    description=TOOL_DESCRIPTION,
    args_schema=CurrentTimeInput,
    metadata=TOOL_METADATA,
)
