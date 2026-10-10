"""根据工具风险等级生成 LangChain 人工确认名单。"""

from langchain.agents.middleware import InterruptOnConfig
from langchain_core.tools import BaseTool

from ai_agent_learning.config import PermissionsConfig
from ai_agent_learning.langchain_agent.tools.risk import RiskLevel

RESTRICTED_RISK_LEVELS: frozenset[RiskLevel] = frozenset(
    {
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
        RiskLevel.CRITICAL,
    }
)


def build_interrupt_on(
    tools: list[BaseTool],
    permissions: PermissionsConfig | None = None,
) -> dict[str, bool | InterruptOnConfig]:
    """生成 HumanInTheLoopMiddleware 使用的工具确认名单。"""
    permissions = permissions or PermissionsConfig()
    confirmation_risk_levels = (
        RESTRICTED_RISK_LEVELS if permissions.mode == "restricted" else frozenset()
    )
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    for tool in tools:
        metadata = tool.metadata or {}
        raw_risk_level = metadata.get("risk_level")
        try:
            risk_level = RiskLevel(raw_risk_level)
        except (TypeError, ValueError) as error:
            raise ValueError(f"工具 {tool.name} 缺少有效的 risk_level") from error

        if risk_level in confirmation_risk_levels:
            interrupt_on[tool.name] = InterruptOnConfig(
                allowed_decisions=["approve", "reject"]
            )

    return interrupt_on
