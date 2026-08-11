from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import AcquisitionAction, CriterionVerdict
from backend.app.domain.values import TypedValue


class AffectedCriterion(StrictModel):
    nct_id: str
    criterion_id: str
    current_verdict: CriterionVerdict
    criticality: Literal["CRITICAL", "NONCRITICAL"]
    current_rank: int


class AnswerBranch(StrictModel):
    branch_id: str
    label: str
    response_kind: Literal["VALUE", "UNKNOWN", "DECLINED", "RETAIN_A", "RETAIN_B", "REVIEW"]
    synthetic_value: TypedValue | None = None
    weight: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def value_matches_response_kind(self) -> AnswerBranch:
        if self.response_kind == "VALUE" and self.synthetic_value is None:
            raise ValueError("VALUE branch requires synthetic_value")
        if self.response_kind != "VALUE" and self.synthetic_value is not None:
            raise ValueError("non-VALUE branch forbids synthetic_value")
        return self


class UtilityComponents(StrictModel):
    mean_risk_reduction: float
    minimum_risk_reduction: float
    mean_decision_resolution: float
    branch_discrimination: float
    coverage: float
    base_utility: float
    burden_penalty: float
    sensitivity_penalty: float
    final_utility: float


class QuestionCandidate(StrictModel):
    question_id: str
    slot_id: str
    action: AcquisitionAction
    answer_type: str
    affected: list[AffectedCriterion]
    branches: list[AnswerBranch] = Field(min_length=2, max_length=6)
    burden_penalty: float
    sensitivity_penalty: float
    utility_components: UtilityComponents | None

    @model_validator(mode="after")
    def branches_are_unique_and_normalized(self) -> QuestionCandidate:
        if len({branch.branch_id for branch in self.branches}) != len(self.branches):
            raise ValueError("branch IDs must be unique")
        expected_ids = [f"{self.question_id}:{index}" for index in range(len(self.branches))]
        if [branch.branch_id for branch in self.branches] != expected_ids:
            raise ValueError("branch IDs must use deterministic question:index labels")
        if abs(sum(branch.weight for branch in self.branches) - 1.0) > 1e-9:
            raise ValueError("branch weights must sum to 1.0")
        return self


class QuestionSelection(StrictModel):
    selected: QuestionCandidate | None
    stop_reason: str | None
    top_alternatives: list[QuestionCandidate]
    patient_facing_question: str | None
    deterministic_rationale: str


class BranchMetrics(StrictModel):
    risk_reduction: float
    decision_resolution: float


class OptimizerRuntimeConfig(StrictModel):
    top_k: int
    max_questions: int
    hard_max_questions: int
    max_branches: int
    stop_utility_threshold: float
    stable_risk_reduction_threshold: float
