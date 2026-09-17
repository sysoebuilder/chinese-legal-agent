"""构建候选记忆提取与新旧记忆判断使用的模型提示词。"""

import json
from datetime import UTC, date, datetime

from ai_agent_learning.memory.schema import (
    MemoryCandidate,
    MemoryCandidateBatch,
    MemoryDecision,
    MemoryDecisionBatch,
    StoredMemory,
)
from ai_agent_learning.schema import Message


def build_memory_instructions(today: date | None = None) -> Message:
    reference_date = today or datetime.now(UTC).date()
    schema = json.dumps(
        MemoryCandidateBatch.model_json_schema(),
        ensure_ascii=False,
    )
    return Message(
        role="system",
        content=(
            "你是长期记忆提取器。请从随后提供的本次对话中提取未来对话仍有用的"
            "用户事实、偏好、目标、学习状态、项目状态，以及助手（AI）的重要行为及其结果。"
            "每条记忆只表达一个事实，并明确行为主体是用户还是助手。"
            "用户相关信息只提取用户明确表达或明确确认的内容，不把助手的建议当作用户事实。"
            "助手相关信息可以来自助手消息，例如完成的代码修改、配置调整、排查结论，"
            "以及对后续对话仍有意义的操作结果；助手行为统一使用 assistant 类型。"
            "严格区分建议、计划、尝试和完成：不能把‘准备修改’记录成‘已经修改’，"
            "失败或未完成的操作不能记为成功。只有助手自述而缺少结果佐证时，"
            "content 应写明‘助手报告……’，不得将其视为已独立验证的事实。"
            "不要记录助手的内部推理、普通回答过程或没有长期价值的工具调用细节。"
            "忽略寒暄、一次性指令、仅对当前回答有用的信息以及密码、API Key、Token、"
            "身份证号、银行卡号等敏感信息。没有合适记忆时返回 memories 空数组。"
            "key 必须使用稳定的英文 snake_case；scope 使用 global 或 project:<项目名>。"
            "temporal_type 只能表示 permanent、persistent、temporary 或 event。"
            "每条记忆必须明确设置 status，不得省略或填写 null：active 表示仍然有效；"
            "superseded 表示已被修正、撤销或废弃；expired 表示已经过期、不再适用。"
            "用户明确撤销某条事实、偏好或目标时，也要提取对应的非 active 候选，"
            "沿用原事实的稳定 key，并保留撤销依据，以便决策器废弃旧记忆。"
            f"判断时参考日期：{reference_date.isoformat()}。"
            "importance 和 confidence 使用 0 到 1。evidence 保留支持该记忆的简短原话；"
            "用户事实引用用户原话，助手行为引用对应助手消息中的原话，不得编造或拼接证据。"
            "只输出符合以下 JSON Schema 的 JSON 对象，不输出 Markdown、解释、代码块或空白响应。"
            "输出的第一个字符必须是 {，最后一个字符必须是 }，"
            "禁止在 JSON 对象之前输出空格或换行。"
            "即使没有可保存的记忆，也必须返回完整 JSON：{\"memories\":[]}。"
            "不得只返回空格、换行、null 或空字符串。"
            "JSON Schema：\n" + schema
        ),
    )


def build_memory_decision_instructions(
    candidate: MemoryCandidate,
    old_memories: list[StoredMemory],
) -> Message:
    """构造一条候选记忆与相关旧记忆的决策提示词。"""
    schema = json.dumps(MemoryDecision.model_json_schema(), ensure_ascii=False)
    old_memories_json = json.dumps(
        [memory.model_dump(mode="json") for memory in old_memories],
        ensure_ascii=False,
    )
    return Message(
        role="system",
        content=(
            "你是长期记忆决策器。请比较候选记忆与相关旧记忆，并只选择一种动作："
            "add 表示候选是独立的新事实；confirm 表示候选与一条旧记忆含义一致；"
            "replace 表示候选修正、更新、合并或废弃旧记忆；ignore 表示候选没有新增价值。"
            "confirm 必须引用一个旧记忆 ID。replace 必须引用所有被替换的旧记忆 ID，"
            "并生成一条完整、独立、忠于现有证据的 replacement。"
            "候选和 replacement 必须包含 status：active 为有效，superseded 为修正、撤销或废弃，"
            "expired 为过期。add 保存候选 status；replace 保存 replacement.status。"
            "confirm 只增加确认次数，不能更新 status；含义一致但状态变化时必须使用 replace。"
            "需要废弃旧记忆时用 replace 引用旧 ID，replacement.status 必须为 superseded 或 expired，"
            "不能将撤销指令当作新的 active 事实单独 add，也不能用 ignore 跳过状态更新。"
            "add 和 ignore 不得包含 target_ids 或 replacement。"
            "只能引用下方提供的旧记忆 ID，不得编造事实或 ID。"
            "只输出符合 JSON Schema 的 JSON 对象，不输出 Markdown。\n"
            f"JSON Schema：{schema}\n"
            f"候选记忆：{candidate.model_dump_json()}\n"
            f"相关旧记忆：{old_memories_json}"
        ),
    )


def build_memory_decisions_instructions(
    candidates_with_memories: list[tuple[MemoryCandidate, list[StoredMemory]]],
) -> Message:
    """构造整批候选记忆与各自相关旧记忆的决策提示词。"""
    schema = json.dumps(MemoryDecisionBatch.model_json_schema(), ensure_ascii=False)
    items = [
        {
            "candidate_index": index,
            "candidate": candidate.model_dump(mode="json"),
            "old_memories": [
                memory.model_dump(mode="json") for memory in old_memories
            ],
        }
        for index, (candidate, old_memories) in enumerate(candidates_with_memories)
    ]
    return Message(
        role="system",
        content=(
            "你是长期记忆批量决策器。请逐条比较候选记忆与该条目提供的相关旧记忆，"
            "每个 candidate_index 必须且只能返回一次决定，不能遗漏、重复或改变索引。"
            "add 表示候选是独立的新事实；confirm 表示候选与一条旧记忆含义一致；"
            "replace 表示候选修正、更新、合并或废弃旧记忆；ignore 表示候选没有新增价值。"
            "没有相关旧记忆时通常使用 add，且不得引用其他条目的旧记忆。"
            "confirm 必须引用当前条目的一条旧记忆 ID。replace 必须引用当前条目中"
            "所有被替换的旧记忆 ID，并生成完整、独立、忠于现有证据的 replacement。"
            "候选和 replacement 必须包含 status：active 为有效，superseded 为修正、撤销或废弃，"
            "expired 为过期。add 保存候选 status；replace 保存 replacement.status。"
            "confirm 只增加确认次数，不能更新 status；含义一致但状态变化时必须使用 replace。"
            "需要废弃旧记忆时用 replace，replacement.status 必须为 superseded 或 expired。"
            "add 和 ignore 不得包含 target_ids 或 replacement。"
            "不得让多个候选对同一个旧记忆执行相互冲突的动作。"
            "只能引用各自条目提供的旧记忆 ID，不得编造事实或 ID。"
            "只输出符合 JSON Schema 的 JSON 对象，不输出 Markdown。\n"
            f"JSON Schema：{schema}\n"
            "待决策条目："
            + json.dumps(items, ensure_ascii=False)
        ),
    )
