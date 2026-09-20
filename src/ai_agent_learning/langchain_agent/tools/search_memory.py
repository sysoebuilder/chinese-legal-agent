"""长期记忆检索工具的 LangChain 适配。"""

from typing import Annotated

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.tools.builtin.search_memory import SPEC, handler


class SearchMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]] = Field(
        min_length=1,
        max_length=5,
    )
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")


search_memory_tool = StructuredTool.from_function(
    coroutine=handler,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=SearchMemoryInput,
    metadata={"risk_level": SPEC.risk_level.value},
)
