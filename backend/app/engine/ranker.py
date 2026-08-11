from __future__ import annotations

from decimal import Decimal

from backend.app.domain.enums import TrialDecision
from backend.app.domain.ranking import TrialEvaluation

_QUANTUM = Decimal("0.00000001")


def build_sort_tuple(evaluation: TrialEvaluation) -> tuple[object, ...]:
    key = evaluation.ranking_key
    proof = key.proof_completeness.quantize(_QUANTUM)
    retrieval = key.retrieval_score.quantize(_QUANTUM)
    if evaluation.decision is TrialDecision.INELIGIBLE:
        return (
            key.tier_order,
            key.verified_fail_count,
            key.critical_unknown_count,
            -proof,
            -retrieval,
            -key.recruitment_status_priority,
            -key.last_update_epoch_days,
            key.nct_id,
        )
    return (
        key.tier_order,
        key.critical_unknown_count,
        -proof,
        -retrieval,
        -key.recruitment_status_priority,
        -key.last_update_epoch_days,
        key.nct_id,
    )


def rank_trials(evaluations: list[TrialEvaluation]) -> list[TrialEvaluation]:
    return sorted(evaluations, key=build_sort_tuple)
