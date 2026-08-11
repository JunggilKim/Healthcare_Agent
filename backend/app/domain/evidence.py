from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import EvidenceGrade
from backend.app.domain.values import TypedValue

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceSpan(StrictModel):
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    sha256: str
    language: Literal["ko", "en", "other"]

    @model_validator(mode="after")
    def offsets_and_hash_are_well_formed(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("source span sha256 must be 64 lowercase hexadecimal characters")
        return self


class PatientFact(StrictModel):
    fact_id: str
    slot_id: str
    value: TypedValue
    grade: EvidenceGrade
    source_spans: list[SourceSpan]
    derived_from_fact_ids: list[str] = Field(default_factory=list)
    transformation_id: str | None = None
    asserted_at: datetime
    effective_date: date | None = None
    admissible_for_hard_decision: bool
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def enforce_evidence_grade_contract(self) -> PatientFact:
        if self.grade is EvidenceGrade.A_DIRECT and not self.source_spans:
            raise ValueError("grade-A fact requires a source span")
        if self.grade is EvidenceGrade.B_DETERMINISTIC:
            if not self.derived_from_fact_ids or self.transformation_id is None:
                raise ValueError("grade-B fact requires parents and transformation_id")
        if self.grade in {EvidenceGrade.C_ONTOLOGY, EvidenceGrade.H_HYPOTHESIS}:
            if self.admissible_for_hard_decision:
                raise ValueError("grade-C/H evidence cannot be hard-admissible")
        return self


class RetrievalHypothesis(StrictModel):
    hypothesis_id: str
    concept: str
    normalized_concept: str
    source_fact_ids: list[str]
    rationale_code: str
    grade: Literal[EvidenceGrade.H_HYPOTHESIS]
    admissible_for_eligibility: Literal[False]


class FactConflict(StrictModel):
    conflict_id: str
    slot_id: str
    fact_ids: list[str] = Field(min_length=2)
    conflict_type: Literal[
        "VALUE_MISMATCH",
        "TEMPORAL_OVERLAP",
        "NEGATION_MISMATCH",
        "UNIT_INCOMPATIBLE",
        "SOURCE_AMBIGUITY",
    ]
    status: Literal["OPEN", "RESOLVED"]
    resolution_fact_id: str | None = None


class PatientState(StrictModel):
    confirmed_facts: list[PatientFact] = Field(default_factory=list)
    retrieval_hypotheses: list[RetrievalHypothesis] = Field(default_factory=list)
    conflicts: list[FactConflict] = Field(default_factory=list)


class EligibilityContext(StrictModel):
    facts: list[PatientFact]
    conflicts: list[FactConflict]


class RetrievalContext(StrictModel):
    facts: list[PatientFact]
    hypotheses: list[RetrievalHypothesis]
