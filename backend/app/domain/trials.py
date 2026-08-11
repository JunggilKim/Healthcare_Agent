from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from backend.app.domain.ast import CriterionAst
from backend.app.domain.base import StrictModel
from backend.app.domain.enums import SourceDirection
from backend.app.domain.evidence import SourceSpan


class TrialLocation(StrictModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    status: str | None = None


class RawTrialRecord(StrictModel):
    nct_id: str
    api_version: str
    retrieved_at: datetime
    source_json_sha256: str
    version_holder: str | None
    last_update_post_date: date | None
    overall_status: str
    study_type: str
    official_title: str | None
    brief_title: str
    conditions: list[str]
    keywords: list[str]
    brief_summary: str | None
    detailed_description: str | None
    eligibility_criteria: str | None
    sex: str | None
    minimum_age: str | None
    maximum_age: str | None
    healthy_volunteers: bool | None
    phases: list[str]
    intervention_names: list[str]
    locations: list[TrialLocation]
    raw_gcs_uri: str | None


class CompiledCriterion(StrictModel):
    criterion_id: str
    nct_id: str
    source_direction: SourceDirection
    source_order: int
    source_span: SourceSpan
    source_text_sha256: str
    normalized_summary: str
    ast: CriterionAst
    required_slots: list[str]
    criticality: Literal["CRITICAL", "NONCRITICAL"]
    compiler_confidence: float = Field(ge=0, le=1)
    protocol_verified: bool
    opaque: bool
    warnings: list[str]


class ProtocolReviewIssue(StrictModel):
    criterion_id: str
    issue_type: Literal[
        "NEGATION_SCOPE",
        "AND_OR_SCOPE",
        "MISSING_CLAUSE",
        "ADDED_ASSUMPTION",
        "THRESHOLD_ERROR",
        "TEMPORAL_ERROR",
        "OTHER",
    ]
    severity: Literal["BLOCKING", "WARNING"]
    source_quote: str
    explanation: str


class ProtocolReviewArtifact(StrictModel):
    review_id: str
    nct_id: str
    criterion_source_hashes: list[str]
    compiled_protocol_hash: str
    review_method: Literal["GEMINI_SEMANTIC_REVIEW", "MANUAL_FIXTURE"]
    reviewer_label: str
    model_id: str | None = None
    prompt_version: str | None = None
    reviewed_at: datetime
    approved: bool
    issues: list[ProtocolReviewIssue] = Field(default_factory=list)
    content_hash: str


class CompiledTrial(StrictModel):
    compiled_trial_id: str
    nct_id: str
    eligibility_text_sha256: str
    criteria: list[CompiledCriterion]
    source_character_coverage: float = Field(ge=0, le=1)
    protocol_verified: bool
    review_artifact_id: str | None
    compiler_model_id: str
    compiler_prompt_version: str
    ast_schema_version: str
    slot_catalog_version: str
    boundary_tests_passed: bool
    warnings: list[str] = Field(default_factory=list)
    content_hash: str
    created_at: datetime
