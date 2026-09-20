"""联网搜索工具的 LangChain 适配。"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.tools.builtin.web_search import SPEC, handler


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=70, pattern=r"\S")
    count: int = Field(default=5, ge=1, le=50)


web_search_tool = StructuredTool.from_function(
    coroutine=handler,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=WebSearchInput,
    metadata={"risk_level": SPEC.risk_level.value},
)
