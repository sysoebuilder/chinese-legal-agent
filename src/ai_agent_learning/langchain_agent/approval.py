"""根据工具风险等级生成 LangChain 人工确认名单。"""

from langchain.agents.middleware import InterruptOnConfig
from langchain_core.tools import BaseTool

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel

# 修改这里即可调整哪些风险等级需要人工确认。
CONFIRMATION_RISK_LEVELS: frozenset[RiskLevel] = frozenset()

# 即使风险等级配置发生变化，这些工具也始终需要人工确认。
ALWAYS_CONFIRM_TOOLS = frozenset({"send_email"})


def build_interrupt_on(
    tools: list[BaseTool],
) -> dict[str, bool | InterruptOnConfig]:
    """生成 HumanInTheLoopMiddleware 使用的工具确认名单。"""
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    for tool in tools:
        metadata = tool.metadata or {}
        raw_risk_level = metadata.get("risk_level")
        try:
            risk_level = RiskLevel(raw_risk_level)
        except (TypeError, ValueError) as error:
            raise ValueError(f"工具 {tool.name} 缺少有效的 risk_level") from error

        if (
            risk_level in CONFIRMATION_RISK_LEVELS
            or tool.name in ALWAYS_CONFIRM_TOOLS
        ):
            interrupt_on[tool.name] = InterruptOnConfig(
                allowed_decisions=["approve", "reject"]
            )

    return interrupt_on
