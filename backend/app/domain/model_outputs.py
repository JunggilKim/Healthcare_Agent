from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from backend.app.domain.ast import CriterionAst
from backend.app.domain.base import StrictModel
from backend.app.domain.enums import SourceDirection
from backend.app.domain.trials import ProtocolReviewIssue
from backend.app.domain.values import TypedValue


class ConflictProposal(StrictModel):
    slot_id: str
    proposal_indexes: list[int] = Field(min_length=2)
    conflict_type: Literal[
        "VALUE_MISMATCH",
        "TEMPORAL_OVERLAP",
        "NEGATION_MISMATCH",
        "UNIT_INCOMPATIBLE",
        "SOURCE_AMBIGUITY",
    ]


class UnparsedSpan(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    reason_code: str


class PatientFactProposal(StrictModel):
    slot_id: str
    value: TypedValue
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    effective_date: date | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class RetrievalHypothesisProposal(StrictModel):
    concept: str
    normalized_concept: str
    source_proposal_indexes: list[int]
    rationale_code: str


class PatientExtractionResult(StrictModel):
    facts: list[PatientFactProposal] = Field(default_factory=list)
    retrieval_hypotheses: list[RetrievalHypothesisProposal] = Field(default_factory=list)
    possible_conflicts: list[ConflictProposal] = Field(default_factory=list)
    unparsed_spans: list[UnparsedSpan] = Field(default_factory=list)
    language: Literal["ko", "en", "other"]


class CriterionCompilationProposal(StrictModel):
    source_direction: SourceDirection
    source_order: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    normalized_summary: str
    ast: CriterionAst
    required_slots: list[str]
    criticality: Literal["CRITICAL", "NONCRITICAL"] = "CRITICAL"
    compiler_confidence: float = Field(ge=0, le=1)
    opaque: bool
    warnings: list[str] = Field(default_factory=list)


class CompiledTrialProposal(StrictModel):
    nct_id: str
    criteria: list[CriterionCompilationProposal]
    unassigned_source_spans: list[UnparsedSpan] = Field(default_factory=list)
    compiler_warnings: list[str] = Field(default_factory=list)


class ProtocolReviewProposal(StrictModel):
    approved: bool
    issues: list[ProtocolReviewIssue] = Field(default_factory=list)
