"""定义候选记忆、已存记忆和记忆决策的数据结构。"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryType(str, Enum):
    ASSISTANT = "assistant"
    PREFERENCE = "preference"
    USER_FACT = "user_fact"
    LEARNING_STATE = "learning_state"
    PROJECT_STATE = "project_state"
    GOAL = "goal"


class TemporalType(str, Enum):
    PERMANENT = "permanent"
    PERSISTENT = "persistent"
    TEMPORARY = "temporary"
    EVENT = "event"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class MemoryAction(str, Enum):
    ADD = "add"
    CONFIRM = "confirm"
    REPLACE = "replace"
    IGNORE = "ignore"


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1)
    type: MemoryType
    key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    scope: str = Field(min_length=1)
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    temporal_type: TemporalType
    status: MemoryStatus
    evidence: str = Field(min_length=1)


class MemoryCandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryCandidate] = Field(default_factory=list)


class StoredMemory(MemoryCandidate):
    """已经写入长期记忆库的正式记忆。"""

    id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime
    confirmation_count: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_payload(cls, value):
        """仅兼容读取旧库已废弃的日期字段，候选输出仍严格遵守新 Schema。"""
        if isinstance(value, dict):
            value = {
                key: item
                for key, item in value.items()
                if key not in {"valid_from", "valid_until"}
            }
        return value


class MemorySearchResult(BaseModel):
    """向量查询返回的正式记忆及其相似度。"""

    memory: StoredMemory
    score: float


class MemoryDecision(BaseModel):
    """决策层针对一条候选记忆返回的结构化决定。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: MemoryAction
    target_ids: list[str] = Field(default_factory=list)
    replacement: MemoryCandidate | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "MemoryDecision":
        if len(self.target_ids) != len(set(self.target_ids)):
            raise ValueError("target_ids 不能重复")
        if self.action in {MemoryAction.ADD, MemoryAction.IGNORE}:
            if self.target_ids or self.replacement is not None:
                raise ValueError("ADD 和 IGNORE 不能包含目标 ID 或替换内容")
        elif self.action is MemoryAction.CONFIRM:
            if len(self.target_ids) != 1 or self.replacement is not None:
                raise ValueError("CONFIRM 必须指定一个目标 ID，且不能包含替换内容")
        elif not self.target_ids or self.replacement is None:
            raise ValueError("REPLACE 必须指定目标 ID 并提供替换内容")
        return self


class IndexedMemoryDecision(BaseModel):
    """批量决策中一条候选记忆对应的决定。"""

    model_config = ConfigDict(extra="forbid")

    candidate_index: int = Field(ge=0)
    decision: MemoryDecision


class MemoryDecisionBatch(BaseModel):
    """模型一次返回的整批记忆决定。"""

    model_config = ConfigDict(extra="forbid")

    decisions: list[IndexedMemoryDecision] = Field(default_factory=list)
