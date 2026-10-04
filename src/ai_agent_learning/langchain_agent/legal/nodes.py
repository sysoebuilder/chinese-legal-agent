"""法律 Agent 子图节点。"""

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.types import interrupt
from openai import APIStatusError

from ai_agent_learning.langchain_agent.legal.schemas import (
    LegalAnswerReviewOutput,
    LegalCaseAnalysisOutput,
    LegalClassificationOutput,
    LegalDecisionOutput,
)
from ai_agent_learning.langchain_agent.legal.state import (
    LegalAgentState,
    LegalCase,
    LegalDecision,
    RetrievedLaw,
)
from ai_agent_learning.langchain_agent.tools import search_laws_tool
from ai_agent_learning.retry import retry_async

MAX_RETRIEVAL_ROUNDS = 50
MAX_INVALID_RETRIEVAL_REDECISIONS = 2
MAX_NODE_RETRIES = 1
RETRYABLE_API_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class LegalNodes:
    """持有当前对话模型和法律工具的节点集合。"""

    def __init__(
        self,
        model: BaseChatModel,
        law_tool: BaseTool = search_laws_tool,
    ) -> None:
        self.model = model
        self.law_tool = law_tool

    async def initialize(self, state: LegalAgentState) -> dict[str, Any]:
        if not self._latest_question(state):
            raise ValueError("法律子图缺少用户原始问题")
        return {
            "cases": dict(state.get("cases", {})),
        }

    async def classify(self, state: LegalAgentState) -> dict[str, Any]:
        current_question = self._latest_question(state)
        previous_ai = self._previous_ai_message(state)
        result = await self._invoke_json(
            LegalClassificationOutput,
            """
判断当前输入是否属于中国法律法规问题，并选择所属案件。
涉及法律权利义务、责任、诉讼、行政处理、合同、劳动、婚姻、刑事或法规适用时为 true。
普通知识、闲聊、编程和不需要法律判断的问题为 false。
非法律问题：case_action=none，case_id=null。
案件 ID 的格式固定为 case_数字，例如 case_1、case_2。
新的独立法律问题：case_action=new，case_id=null；不得自行生成 ID，由程序按上述格式生成。
追问、补充或纠正已有案件：case_action=continue，case_id 必须从输入 cases 的键中原样复制。
不得改写编号、只输出数字、使用不存在的 ID，或根据格式猜测 ID。
结合 current_question、最近一次助手回复和已有案件语境，识别“那怎么办”等省略表达，
判断当前输入是否在延续法律讨论；不要仅凭单个词语或最近一次助手回复强行归类。
previous_ai 仅提供语境，判断对象始终是 current_question；没有上一轮助手回复时它为空字符串。
active_case_id 只是最近案件的参考，不能据此强行归类。
不同事件即使涉及同类法律也应新建案件；不得编造 case_id。只输出规定结构的 JSON。
""".strip(),
            json.dumps(
                {
                    "current_question": current_question,
                    "previous_ai": previous_ai,
                    "active_case_id": state.get("active_case_id"),
                    "cases": {
                        case_id: {
                            "original_question": case["original_question"],
                            "user_supplements": case["user_supplements"],
                            "objective_facts": case["objective_facts"],
                        }
                        for case_id, case in state.get("cases", {}).items()
                    },
                },
                ensure_ascii=False,
            ),
            stream=True,
        )
        if result.case_action == "continue" and result.case_id not in state.get("cases", {}):
            raise ValueError("分类模型选择了不存在的 case_id")
        if result.case_action == "none":
            return {"active_case_id": None}
        if result.case_action == "continue":
            assert result.case_id is not None  # 结构化校验已保证继续案件包含 ID。
            case = state["cases"][result.case_id].copy()
            case["current_question"] = current_question
            case["user_supplements"] = [
                *case["user_supplements"],
                current_question,
            ]
            case["retrieval_round"] = 0
            case["pending_retrieval_cases"] = []
            case["usable_laws"] = []
            case["decision"] = None
            case["final_answer"] = None
            case["refusal_reason"] = None
            case["retrieval_feedback"] = None
            case["invalid_retrieval_count"] = 0
            case["retry_target"] = None
            case["retry_count"] = 0
            return {
                "active_case_id": result.case_id,
                "cases": {**state["cases"], result.case_id: case},
            }
        case_id = self._next_case_id(state.get("cases", {}))
        new_case = self._new_case(current_question)
        return {
            "active_case_id": case_id,
            "cases": {**state.get("cases", {}), case_id: new_case},
        }

    async def analyze_case(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        result = await self._invoke_json(
            LegalCaseAnalysisOutput,
            """
从用户原话和补充信息中整理中国法律判断所需的完整客观事实。
不得猜测、补充或预设用户没有陈述的事实和法律结论。
只输出 objective_facts，不生成检索条件或法律结论。输出严格 JSON。
""".strip(),
            json.dumps(
                {
                    "current_question": case["current_question"],
                    "original_question": case["original_question"],
                    "user_supplements": case["user_supplements"],
                },
                ensure_ascii=False,
            ),
            stream=True,
        )
        return self._update_case(
            state,
            objective_facts=result.objective_facts,
            pending_retrieval_cases=[],
            decision=None,
            retrieval_feedback=None,
            invalid_retrieval_count=0,
            retry_target=None,
            retry_count=0,
        )

    async def retrieve_laws(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        cases = case["pending_retrieval_cases"]
        if not cases:
            retry_count = case.get("invalid_retrieval_count", 0) + 1
            if retry_count > MAX_INVALID_RETRIEVAL_REDECISIONS:
                return self._update_case(
                    state,
                    refusal_reason="多次重新决策后仍读取不到有效的检索关键词。",
                )
            return self._update_case(
                state,
                retrieval_feedback="读取不到有效的检索关键词，请重新决定并填写符合结构的 retrieval_cases。",
                invalid_retrieval_count=retry_count,
                refusal_reason=None,
            )
        next_round = case["retrieval_round"] + 1
        if next_round > MAX_RETRIEVAL_ROUNDS:
            return self._update_case(
                state,
                refusal_reason="法律检索已达到最大轮次，仍无法取得充分依据。",
            )
        try:
            result = await self.law_tool.ainvoke({"retrieval_cases": cases})
            laws = self._flatten_laws(result)
        except Exception as error:  # noqa: BLE001 -- 子图根据状态决定是否重试。
            return self._retry_or_refuse(state, "retrieve_laws", error)
        return self._update_case(
            state,
            retrieval_round=next_round,
            retrieval_queries=[*case["retrieval_queries"], *cases],
            pending_retrieval_cases=[],
            retrieved_laws=self._merge_laws(case["retrieved_laws"], laws),
            retrieval_feedback=None,
            invalid_retrieval_count=0,
            retry_target=None,
            retry_count=0,
            refusal_reason=None,
        )

    async def decide(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        result, retry_update = await self._invoke_json_with_retry_state(
            state,
            "decide",
            LegalDecisionOutput,
            f"""
你是法律检索流程的决策模型。当前日期是 {datetime.now(UTC).date().isoformat()}。
只根据用户原话、客观事实和检索结果决定下一步，不得直接编写最终回答。

action 只能是：
- ask_user：缺少时间、地域或关键案件事实，必须提供 question。
- retrieve_more：尚未检索或现有检索不足，必须提供 retrieval_cases。
- final_answer：已经找到足以支持回答的适用法律依据，并已完成下述依据选择，才能选择此动作。
- refuse：数据库无结果、依据无法确认或不能安全形成法律结论。

retrieval_round 为 0 且缺少法律依据时，不得以数据库无结果为由拒答；应选择 retrieve_more。
如果缺少的用户事实会影响检索条件或核心结论，应先选择 ask_user。
输入中的 retrieval_query_history 是此前已经使用的争议点及标题、内容关键词。
若 retrieval_feedback 提示读取不到检索关键词，请重新决定；需要检索时重新填写符合结构的 retrieval_cases。
若 retry_feedback 存在，说明上一次决策调用失败；请重新独立完成本轮决定。
首次检索时，先根据客观事实梳理不同争议点及所需的全国性、地方性和程序性依据，
由你生成检索条件；事实分析节点不会预先生成检索条件。
如果尚未找到所需法律依据，选择 retrieve_more 时应针对具体缺口从不同角度填写关键词，
例如改用法律概念、相关行为、适用地域或法规名称，并尝试不同表述。
每次 retrieval_cases 最多 5 个；先全面检查哪些依据仍缺失，再把实质不同的角度尽量放在同一批。
同一角度的近义词放在一项的关键词数组中，不要为了凑数量拆成多个相似项。
不要重复或仅微调历史检索条件；如果没有实质不同的检索角度，应根据现有证据选择
ask_user 或 refuse，不要继续重复检索。

选择 final_answer 时必须同时满足：
1. 从输入 retrieved_laws 列表中选择与本次问题相关且适用的记录。
2. 输出 usable_law_ids 字段，类型为非空字符串数组，至少包含一个所选记录的顶层 id。
   将每个 id 原样复制为字符串，不得改写、截断或自行生成；不要使用 payload 内的字段、
   法规名称、条文编号、列表序号或 issue 代替 id。
3. usable_law_ids 中的每个值都必须与 retrieved_laws 中某条记录的 id 完全一致。
   程序会按 id 精确匹配并生成 usable_laws；匹配不到就没有可供最终回答使用的依据。
   usable_laws 由程序生成，不是你需要输出的字段。
4. reason 简要说明所选依据为什么能够支持回答；仅在 reason 中提及法规不能代替填写 usable_law_ids。
5. 输出前检查 usable_law_ids 未遗漏、不是 null 或空数组，且全部来自本次输入。
   如果无法选出有效 id，不得选择 final_answer；根据缺失信息选择 ask_user、retrieve_more 或 refuse。

地方性规定只有在用户地域信息足够时才可选用。失效、尚未生效、时效未知或关键字段缺失的法规，
不得作为确定结论依据。输出严格 JSON。
""".strip(),
            self._decision_context(state),
            stream=True,
        )
        if retry_update is not None:
            return retry_update
        decision = result.model_dump(exclude_none=True)
        if result.action == "retrieve_more" and case["retrieval_round"] >= MAX_RETRIEVAL_ROUNDS:
            decision = {
                "action": "refuse",
                "reason": "已达到最大法律检索轮次，仍缺少充分依据。",
            }

        usable_laws: list[RetrievedLaw] = []
        if decision["action"] == "final_answer":
            selected_ids = set(decision.get("usable_law_ids", []))
            usable_laws = [
                law for law in case["retrieved_laws"] if law["id"] in selected_ids
            ]
            if not usable_laws:
                decision = {
                    "action": "refuse",
                    "reason": "决策模型没有选中可验证的法律依据。",
                }
        return self._update_case(
            state,
            decision=decision,
            pending_retrieval_cases=(
                decision.get("retrieval_cases", [])
                if decision["action"] == "retrieve_more"
                else []
            ),
            usable_laws=usable_laws,
            retrieval_feedback=None,
            retry_target=None,
            retry_count=0,
        )

    async def ask_user(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        decision = case["decision"]
        question = str(decision.get("question") or "").strip() if decision else ""
        if not question:
            return self._update_case(
                state,
                refusal_reason="法律决策缺少需要向用户询问的问题。",
            )
        response = interrupt(
            {
                "type": "legal_information_required",
                "question": question,
            }
        )
        if isinstance(response, dict):
            answer = str(response.get("answer") or "").strip()
        else:
            answer = str(response).strip()
        if not answer:
            return self._update_case(
                state,
                refusal_reason="用户没有补充法律分析所需的信息。",
            )
        update = self._update_case(
            state,
            current_question=answer,
            user_supplements=[*case["user_supplements"], answer],
            decision=None,
        )
        update["messages"] = [HumanMessage(content=answer)]
        return update

    async def generate_final_answer(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        if not case["usable_laws"]:
            return self._update_case(
                state,
                refusal_reason="没有经过决策模型确认的可用法律依据。",
            )
        answer_context = {
            "current_question": case["current_question"],
            "original_question": case["original_question"],
            "user_supplements": case["user_supplements"],
            "objective_facts": case["objective_facts"],
            "usable_laws": case["usable_laws"],
        }
        messages = [
            SystemMessage(
                content=(
                    "你负责根据当前案件状态和已确认的法律检索结果回答用户本轮 current_question。"
                    "只能引用提供的法律依据，不得补充未检索到的条文。"
                    "说明适用时间、地域和不确定性；信息不足时明确限制结论。"
                )
            ),
            HumanMessage(
                content=(
                    "以下是本轮法律状态：\n"
                    + json.dumps(
                        answer_context,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            ),
        ]
        review, retry_update = await self._invoke_json_with_retry_state(
            state,
            "generate_final_answer",
            LegalAnswerReviewOutput,
            """
在最终法律回答展示前检查本轮事实与已选法律依据是否足够。不要输出正文。
action 只能是：
- answer：现有事实和 usable_laws 足以支持对用户核心问题的回答。
- ask_user：缺少会影响核心结论的事实，question 必须明确询问缺少的信息。
- retrieve_more：缺少支持核心结论的法律依据，retrieval_cases 必须提供针对缺口的检索条件。
- refuse：现有依据不足且历史检索已覆盖相关角度，无法可靠回答。
选择 retrieve_more 时，retrieval_cases 最多 5 个；全面列出本轮尚缺的不同法律依据，
将实质不同的检索角度尽量放在同一批，不要把近义词拆成重复项。
输入中的 retrieval_query_history 是此前已检索过的争议点和关键词。
不要重复或仅微调历史检索条件；若没有实质不同的检索角度，
应根据现有依据选择 answer、ask_user 或 refuse，不要继续检索。
reason 说明通过检查的理由或具体缺口。不能把上游已选择最终回答当成依据充分的证明。
区分关键缺口与一般办理建议；不影响核心回答的材料准备、后续行动建议不必回退。
不要重复询问用户已经提供的信息，也不要猜测事实或编造未检索到的法律。
""".strip(),
            json.dumps(
                {
                    **answer_context,
                    "retrieval_query_history": self._retrieval_query_history(case),
                },
                ensure_ascii=False,
                default=str,
            ),
            stream=True,
        )
        if retry_update is not None:
            return retry_update
        if review.action != "answer":
            if review.action == "refuse":
                return self._update_case(
                    state,
                    refusal_reason=review.reason,
                )
            if case["retrieval_round"] >= MAX_RETRIEVAL_ROUNDS:
                return self._update_case(
                    state,
                    refusal_reason=f"已达到最大法律检索轮次：{review.reason}",
                )
            return self._update_case(
                state,
                decision=review.model_dump(exclude_none=True),
                pending_retrieval_cases=(
                    [item.model_dump() for item in review.retrieval_cases]
                    if review.action == "retrieve_more"
                    else []
                ),
                usable_laws=[],
                final_answer=None,
                refusal_reason=None,
                retry_target=None,
                retry_count=0,
            )
        messages[0].content += "最终回答前的检查意见：" + review.reason
        answer_parts: list[str] = []
        try:
            async for chunk in self.model.astream(messages):
                if chunk.text:
                    answer_parts.append(chunk.text)
        except Exception as error:  # noqa: BLE001 -- 子图根据状态决定是否重试。
            return self._retry_or_refuse(state, "generate_final_answer", error)
        answer = "".join(answer_parts).strip()
        if not answer:
            return self._update_case(
                state,
                refusal_reason="模型没有生成有效法律回答。",
            )
        update = self._update_case(
            state,
            final_answer=answer,
            retry_target=None,
            retry_count=0,
        )
        update["messages"] = [AIMessage(content=answer)]
        return update

    async def refuse(self, state: LegalAgentState) -> dict[str, Any]:
        case = self._active_case(state)
        decision: LegalDecision = case["decision"] or {
            "action": "refuse",
            "reason": "无法取得足以支持法律结论的有效依据。",
        }
        reason = case["refusal_reason"] or decision.get("reason")
        answer = f"无法提供确定的法律结论：{reason}"
        update = self._update_case(
            state,
            final_answer=answer,
            refusal_reason=reason,
        )
        update["messages"] = [AIMessage(content=answer)]
        return update

    def _retry_or_refuse(
        self,
        state: LegalAgentState,
        node_name: str,
        error: Exception,
    ) -> dict[str, Any]:
        case = self._active_case(state)
        error_detail = type(error).__name__
        if isinstance(error, APIStatusError):
            error_detail += f"（HTTP {error.status_code}）"
        retryable = not isinstance(error, APIStatusError) or (
            error.status_code in RETRYABLE_API_STATUS_CODES
        )
        retry_count = (
            case.get("retry_count", 0) + 1
            if case.get("retry_target") == node_name
            else 1
        )
        if retryable and retry_count <= MAX_NODE_RETRIES:
            return self._update_case(
                state,
                retry_target=node_name,
                retry_count=retry_count,
                last_node_error=f"{node_name}：{error_detail}",
                refusal_reason=None,
            )
        return self._update_case(
            state,
            retry_target=None,
            retry_count=0,
            last_node_error=f"{node_name}：{error_detail}",
            refusal_reason=f"{node_name} 执行失败：{error_detail}",
        )

    async def _invoke_json_with_retry_state(
        self,
        state: LegalAgentState,
        node_name: str,
        schema: type[Any],
        system: str,
        content: str,
        *,
        stream: bool = False,
    ) -> tuple[Any, dict[str, Any] | None]:
        try:
            if stream:
                return await self._invoke_json(schema, system, content, stream=True), None
            return await self._invoke_json(schema, system, content), None
        except Exception as error:  # noqa: BLE001 -- 子图根据状态决定是否重试。
            return None, self._retry_or_refuse(state, node_name, error)

    async def _invoke_json(
        self, schema: type[Any], system: str, content: str, *, stream: bool = False
    ) -> Any:
        system += (
            "\n\n只输出符合以下 JSON Schema 的 JSON 对象，"
            "遵守必填字段、字段类型和额外字段限制，不要输出 Markdown 代码块：\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        messages = [SystemMessage(content=system), HumanMessage(content=content)]
        if stream:
            parts: list[str] = []
            async for chunk in self.model.astream(
                messages,
                config={"tags": ["legal_structured"]},
                response_format={"type": "json_object"},
            ):
                if chunk.text:
                    parts.append(chunk.text)
            response_text = "".join(parts).strip()
            if not response_text:
                raise ValueError(f"{schema.__name__} 模型响应为空")
            return schema.model_validate_json(response_text)

        async def invoke_model() -> Any:
            return await self.model.ainvoke(
                messages,
                stream=False,
                response_format={"type": "json_object"},
            )

        response = await retry_async(invoke_model, max_attempts=3)
        text = response.text.strip()
        if not text:
            raise ValueError(f"{schema.__name__} 模型响应为空")
        return schema.model_validate_json(text)

    @staticmethod
    def _flatten_laws(result: Any) -> list[RetrievedLaw]:
        if not isinstance(result, dict):
            raise TypeError("法律工具返回值不是对象")
        groups = result.get("results")
        if not isinstance(groups, list):
            raise TypeError("法律工具返回值缺少 results 列表")
        output: list[RetrievedLaw] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("laws"), list):
                continue
            issue = str(group.get("issue") or "").strip()
            for law in group["laws"]:
                if not isinstance(law, dict) or not law.get("id"):
                    continue
                output.append(
                    {
                        "issue": issue,
                        "id": str(law["id"]),
                        "payload": law.get("payload"),
                        "bm25_score": law.get("bm25_score"),
                        "vector_score": law.get("vector_score"),
                    }
                )
        return output

    @staticmethod
    def _merge_laws(
        existing: list[RetrievedLaw],
        incoming: list[RetrievedLaw],
    ) -> list[RetrievedLaw]:
        merged = {law["id"]: law for law in existing}
        for law in incoming:
            merged[law["id"]] = law
        return list(merged.values())

    @staticmethod
    def _decision_context(state: LegalAgentState) -> str:
        case = LegalNodes._active_case(state)
        return json.dumps(
            {
                "current_question": case["current_question"],
                "original_question": case["original_question"],
                "user_supplements": case["user_supplements"],
                "objective_facts": case["objective_facts"],
                "retrieval_round": case["retrieval_round"],
                "retrieval_feedback": case.get("retrieval_feedback"),
                "retry_feedback": (
                    case.get("last_node_error")
                    if case.get("retry_target") == "decide"
                    else None
                ),
                "retrieval_query_history": LegalNodes._retrieval_query_history(case),
                "retrieved_laws": case["retrieved_laws"],
            },
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _retrieval_query_history(case: LegalCase) -> list[dict[str, Any]]:
        return [
            {
                "issue": query["issue"],
                "title_keywords": query["title_keywords"],
                "content_keywords": query["content_keywords"],
            }
            for query in case["retrieval_queries"]
        ]

    @staticmethod
    def _latest_question(state: LegalAgentState) -> str:
        return next(
            (
                message.text.strip()
                for message in reversed(state.get("messages", []))
                if isinstance(message, HumanMessage) and message.text.strip()
            ),
            "",
        )

    @staticmethod
    def _previous_ai_message(state: LegalAgentState) -> str:
        messages = state.get("messages", [])
        current_index = next(
            (
                index for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage) and messages[index].text.strip()
            ),
            None,
        )
        if current_index is None:
            return ""
        previous_index = next(
            (
                index for index in range(current_index - 1, -1, -1)
                if isinstance(messages[index], HumanMessage) and messages[index].text.strip()
            ),
            None,
        )
        if previous_index is None:
            return ""
        return next(
            (
                message.text.strip()
                for message in reversed(messages[previous_index + 1:current_index])
                if isinstance(message, AIMessage) and message.text.strip()
            ),
            "",
        )

    @staticmethod
    def _new_case(question: str) -> LegalCase:
        return {
            "current_question": question,
            "original_question": question,
            "user_supplements": [],
            "objective_facts": [],
            "retrieval_round": 0,
            "pending_retrieval_cases": [],
            "retrieval_feedback": None,
            "invalid_retrieval_count": 0,
            "retry_target": None,
            "retry_count": 0,
            "last_node_error": None,
            "retrieval_queries": [],
            "retrieved_laws": [],
            "usable_laws": [],
            "decision": None,
            "final_answer": None,
            "refusal_reason": None,
        }

    @staticmethod
    def _next_case_id(cases: dict[str, LegalCase]) -> str:
        indexes = [
            int(case_id.removeprefix("case_"))
            for case_id in cases
            if case_id.startswith("case_")
            and case_id.removeprefix("case_").isdigit()
        ]
        return f"case_{max(indexes, default=0) + 1}"

    @staticmethod
    def _active_case(state: LegalAgentState) -> LegalCase:
        case_id = state.get("active_case_id")
        if not case_id or case_id not in state.get("cases", {}):
            raise ValueError("法律流程没有有效的当前案件")
        return state["cases"][case_id]

    @staticmethod
    def _update_case(state: LegalAgentState, **changes: Any) -> dict[str, Any]:
        case_id = state.get("active_case_id")
        case = dict(LegalNodes._active_case(state))
        case.update(changes)
        return {"cases": {**state["cases"], case_id: case}}
