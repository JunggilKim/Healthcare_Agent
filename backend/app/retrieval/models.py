from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from backend.app.domain.base import StrictModel
from backend.app.domain.trials import RawTrialRecord


class ConditionQuery(StrictModel):
    text: str = Field(max_length=300)
    source_fact_ids: list[str] = Field(default_factory=list)
    source_hypothesis_ids: list[str] = Field(default_factory=list)
    priority: int = Field(ge=1)

    @field_validator("text")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("condition query text must not be blank")
        return normalized


class RetrievalQuery(StrictModel):
    condition_queries: list[ConditionQuery] = Field(min_length=1, max_length=4)
    dense_query: str = Field(max_length=800)
    must_not_use_as_eligibility_evidence: Literal[True] = True

    @field_validator("dense_query")
    @classmethod
    def require_non_blank_dense_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dense query must not be blank")
        return normalized


class RegistryCandidate(StrictModel):
    trial: RawTrialRecord
    registry_rank: int = Field(ge=1)
    retrieved_by_queries: list[str]


class RankedCandidate(StrictModel):
    nct_id: str
    registry_rank: int
    bm25_rank: int
    embedding_rank: int | None
    exact_condition_match: bool
    lexical_rrf: float
    full_rrf: float | None
    retrieval_score: float = Field(ge=0, le=1)
    trial: RawTrialRecord
    compiled: bool = False
    compilation_status: Literal["NOT_COMPILED", "OPAQUE_REVIEW_REQUIRED", "VERIFIED"] = (
        "NOT_COMPILED"
    )


class RetrievalResult(StrictModel):
    mode: Literal["live", "snapshot", "hybrid_degraded"]
    api_version: str
    registry_data_timestamp: str
    retrieved_at: datetime
    dense_source_used: bool
    degradation_codes: list[str]
    ranked_candidates: list[RankedCandidate] = Field(max_length=20)
    selected_for_compilation: list[str] = Field(max_length=8)
