"""工具主动声明的结构化错误。"""

from dataclasses import dataclass
from enum import Enum


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN_OUTCOME = "unknown_outcome"


@dataclass(frozen=True)
class ToolFailure:
    code: str
    kind: FailureKind
    message: str


class ToolOperationError(RuntimeError):
    def __init__(self, code: str, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.failure = ToolFailure(code=code, kind=kind, message=message)
