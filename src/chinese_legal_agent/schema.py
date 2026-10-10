from typing import Literal
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)


class ModelRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: str = Field(min_length=1)
