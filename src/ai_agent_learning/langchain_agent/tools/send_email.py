"""邮件发送工具的 LangChain 适配。"""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.tools.builtin.send_email import SPEC, handler


class SendEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    attachment_path: str | None = Field(default=None, min_length=1)


send_email_tool = StructuredTool.from_function(
    coroutine=handler,
    name=SPEC.name,
    description=SPEC.description,
    args_schema=SendEmailInput,
    metadata={
        "risk_level": SPEC.risk_level.value,
        "has_side_effects": SPEC.has_side_effects,
    },
)
