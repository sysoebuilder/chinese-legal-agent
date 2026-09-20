"""法律法规检索工具的 LangChain 适配。"""

from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.tools.builtin.search_raws import SPEC, handler


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(min_length=1)
    title_keywords: list[Annotated[str, Field(min_length=1)]]
    content_keywords: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=1
    )
    case_facts: str = Field(min_length=1)


class SearchRawsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval_cases: list[RetrievalCase] = Field(min_length=1, max_length=5)


async def search_raws(retrieval_cases: list[RetrievalCase]) -> dict[str, Any]:
    """把 Pydantic 参数转换回旧 handler 接受的字典结构。"""
    return await handler([item.model_dump() for item in retrieval_cases])


search_raws_tool = StructuredTool.from_function(
    coroutine=search_raws,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=SearchRawsInput,
    metadata={"risk_level": SPEC.risk_level.value},
)
