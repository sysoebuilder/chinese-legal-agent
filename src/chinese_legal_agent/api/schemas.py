"""HTTP 请求与可恢复的运行状态。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "succeeded",
    "failed",
    "cancelled",
]
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=100_000)
    thread_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


class ResumeRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # key 为 run.state.interrupts 中的中断 ID，value 为审批或补充信息。
    responses: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PendingInterrupt(BaseModel):
    id: str
    value: dict[str, Any]


class SessionView(BaseModel):
    thread_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class SessionPage(BaseModel):
    sessions: list[SessionView]
    has_more: bool
    next_offset: int | None = None


class HistoryMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str


class HistoryPage(BaseModel):
    thread_id: UUID
    messages: list[HistoryMessage]
    has_more: bool
    next_before: str | None = None


class RunView(BaseModel):
    run_id: UUID
    thread_id: UUID
    status: RunStatus
    provider: str
    model: str
    cancel_requested: bool
    interrupts: list[PendingInterrupt] = Field(default_factory=list)
    answer: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    generation: int = 1
    revision: int = 0


class RecoveryView(BaseModel):
    run: RunView | None = None
    can_reconnect: bool = False
    busy: bool = False
    pending_nodes: list[str] = Field(default_factory=list)


def validate_legal_response(response: dict[str, Any]) -> None:
    answer = response.get("answer")
    if set(response) != {"answer"} or not isinstance(answer, str) or not answer.strip():
        raise ValueError("补充信息必须提供非空 answer")


def validate_tool_response(response: dict[str, Any], actions: list[Any]) -> None:
    decisions = response.get("decisions")
    if (
        set(response) != {"decisions"}
        or not isinstance(decisions, list)
        or len(decisions) != len(actions)
    ):
        raise ValueError("decisions 必须逐项对应待审批工具")
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("type") not in {
            "approve",
            "reject",
        }:
            raise ValueError("工具审批只支持 approve 或 reject")
        if set(decision) - {"type", "message"}:
            raise ValueError("不能通过审批接口修改工具参数")
        if "message" in decision and not isinstance(decision["message"], str):
            raise ValueError("拒绝原因必须是字符串")


def validate_responses(
    interrupts: list[dict[str, Any]],
    responses: dict[str, dict[str, Any]],
) -> None:
    if set(responses) != {item["id"] for item in interrupts}:
        raise ValueError("必须回应全部当前中断，不能提交未知或过期中断 ID")
    for item in interrupts:
        value = item["value"]
        response = responses[item["id"]]
        if value.get("type") == "legal_information_required":
            validate_legal_response(response)
            continue
        if "action_requests" in value:
            validate_tool_response(response, value["action_requests"])
            continue
        raise ValueError("不支持的中断类型")
