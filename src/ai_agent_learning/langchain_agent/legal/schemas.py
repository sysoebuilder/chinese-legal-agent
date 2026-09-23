"""法律节点调用模型时使用的结构化输出。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegalClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_action: Literal["new", "continue", "none"]
    case_id: str | None = None

    @model_validator(mode="after")
    def validate_case_selection(self) -> "LegalClassificationOutput":
        if self.case_action == "none":
            if self.case_id is not None:
                raise ValueError("非法律问题不能指定 case_id")
        elif self.case_action == "continue":
            if not (self.case_id or "").strip():
                raise ValueError("继续案件必须提供 case_id")
        elif self.case_id is not None:
            raise ValueError("新法律案件必须选择 new，case_id 由程序生成")
        return self


class LegalRetrievalCaseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(min_length=1)
    title_keywords: list[str]
    content_keywords: list[str] = Field(min_length=1)
    case_facts: str = Field(min_length=1)


class LegalCaseAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_facts: list[str]


class LegalDecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ask_user", "retrieve_more", "final_answer", "refuse"]
    reason: str = Field(min_length=1)
    question: str | None = None
    retrieval_cases: list[LegalRetrievalCaseOutput] = Field(default_factory=list)
    usable_law_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "LegalDecisionOutput":
        if self.action == "ask_user" and not (self.question or "").strip():
            raise ValueError("ask_user 决策必须提供 question")
        if self.action == "retrieve_more" and not self.retrieval_cases:
            raise ValueError("retrieve_more 决策必须提供 retrieval_cases")
        if self.action == "final_answer" and not self.usable_law_ids:
            raise ValueError("final_answer 决策必须提供 usable_law_ids")
        return self


class LegalAnswerReviewOutput(BaseModel):
    """最终正文开始输出前的检查结果。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["answer", "ask_user", "retrieve_more"]
    reason: str = Field(min_length=1)
    question: str | None = None
    retrieval_cases: list[LegalRetrievalCaseOutput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "LegalAnswerReviewOutput":
        if self.action == "ask_user" and not (self.question or "").strip():
            raise ValueError("ask_user 必须提供 question")
        if self.action == "retrieve_more" and not self.retrieval_cases:
            raise ValueError("retrieve_more 必须提供 retrieval_cases")
        return self
