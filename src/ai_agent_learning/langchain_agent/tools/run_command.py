"""PowerShell 命令工具的 LangChain 适配。"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.tools.builtin.run_command import (
    MAX_TIMEOUT_SECONDS,
    SPEC,
    handler,
)


class RunCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    working_directory: str = Field(min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=MAX_TIMEOUT_SECONDS)


run_command_tool = StructuredTool.from_function(
    coroutine=handler,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=RunCommandInput,
    metadata={
        "risk_level": SPEC.risk_level.value,
        "has_side_effects": True,
    },
)
