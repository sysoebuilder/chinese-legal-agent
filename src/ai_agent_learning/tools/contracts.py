from dataclasses import dataclass
from typing import Any
from enum import Enum

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ToolSpec():
    name: str
    description: str
    risk_level: RiskLevel
    input_schema: dict[str, Any]
    has_side_effects: bool = False
    # True 表示工具实现保证重复执行不会产生额外副作用。
    supports_idempotent_retry: bool = False
    # 管理子进程的工具自行实施超时和清理，执行器不提前取消它。
    manages_own_timeout: bool = False

@dataclass
class ToolCall():
    id: str
    name: str
    arguments: dict[str, Any]
