"""当前时间工具的 LangChain 适配。"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from ai_agent_learning.tools.builtin.current_time import SPEC, handler


class CurrentTimeInput(BaseModel):
    """当前时间工具没有输入参数。"""

    model_config = ConfigDict(extra="forbid")


current_time_tool = StructuredTool.from_function(
    coroutine=handler,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=CurrentTimeInput,
    metadata={"risk_level": SPEC.risk_level.value},
)
