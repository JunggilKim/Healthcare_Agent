from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import AcquisitionAction, CriterionVerdict, TrialDecision
from backend.app.domain.model_outputs import ConflictProposal, PatientFactProposal


class QuestionRenderProposal(StrictModel):
    question_id: str
    slot_id: str
    action: AcquisitionAction
    answer_type: str
    question_ko: str
    reason_ko: str


class AnswerInterpretationProposal(StrictModel):
    facts: list[PatientFactProposal] = Field(default_factory=list)
    conflicts: list[ConflictProposal] = Field(default_factory=list)
    unknown: bool = False
    declined: bool = False


class CriterionExplanation(StrictModel):
    criterion_id: str
    verdict: CriterionVerdict
    summary_ko: str
    evidence_refs: list[str]


class TrialReportProposal(StrictModel):
    trial_id: str
    status: TrialDecision
    summary_ko: str
    criterion_refs: list[str]
    evidence_refs: list[str]


class ValidatedReport(StrictModel):
    report: TrialReportProposal
    source: Literal["MODEL_VALIDATED", "DETERMINISTIC_TEMPLATE"]
    rejection_code: str | None = None
