from __future__ import annotations

from decimal import Decimal

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import TrialDecision


class RankingKey(StrictModel):
    tier_order: int
    verified_fail_count: int
    critical_unknown_count: int
    proof_completeness: Decimal
    retrieval_score: Decimal
    recruitment_status_priority: int
    last_update_epoch_days: int
    nct_id: str


class TrialEvaluation(StrictModel):
    session_id: str
    patient_state_version: int
    nct_id: str
    criterion_proof_ids: list[str]
    decision: TrialDecision
    retrieval_score: float
    proof_completeness: float
    critical_unknown_count: int
    verified_fail_count: int
    conflict_count: int
    opaque_critical_count: int
    ranking_key: RankingKey
    display_score: float
    degradation_codes: list[str]
