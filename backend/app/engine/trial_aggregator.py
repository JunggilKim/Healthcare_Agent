from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.domain.enums import CriterionVerdict, TrialDecision
from backend.app.domain.evidence import PatientFact
from backend.app.domain.proof import ProofPacket
from backend.app.domain.ranking import RankingKey, TrialEvaluation
from backend.app.domain.trials import CompiledTrial, RawTrialRecord

_TIER_ORDER = {
    TrialDecision.PRE_SCREEN_PASS: 1,
    TrialDecision.POTENTIAL_MATCH: 2,
    TrialDecision.REVIEW_REQUIRED: 3,
    TrialDecision.INELIGIBLE: 4,
    TrialDecision.IRRELEVANT: 5,
}
_STATUS_PRIORITY = {"RECRUITING": 3, "NOT_YET_RECRUITING": 2, "ENROLLING_BY_INVITATION": 1}
_STATUS_SCORE = {"RECRUITING": 1.0, "NOT_YET_RECRUITING": 0.7, "ENROLLING_BY_INVITATION": 0.4}


def is_trial_irrelevant(
    *,
    retrieval_score: float,
    exact_condition_match: bool,
    compiled_trial: CompiledTrial,
    facts: list[PatientFact],
) -> bool:
    """Apply the conservative three-part irrelevance gate from the release contract."""

    confirmed_slots = {fact.slot_id for fact in facts}
    compiled_condition_slots = {
        slot_id
        for criterion in compiled_trial.criteria
        for slot_id in criterion.required_slots
        if slot_id.startswith(("condition.", "diagnosis.", "pathology.histology"))
    }
    return (
        retrieval_score < 0.15
        and not exact_condition_match
        and not bool(confirmed_slots & compiled_condition_slots)
    )


def _proof_is_complete(packet: ProofPacket) -> bool:
    decision_checks = [
        check for check in packet.verifier_checks if check.check_id != "PV-015" and check.applicable
    ]
    return bool(decision_checks) and all(check.passed for check in decision_checks)


def aggregate_trial(
    *,
    session_id: str,
    patient_state_version: int,
    compiled_trial: CompiledTrial,
    raw_trial: RawTrialRecord,
    proofs: list[ProofPacket],
    retrieval_score: float,
    irrelevant: bool = False,
) -> TrialEvaluation:
    proof_by_criterion = {proof.criterion_id: proof for proof in proofs}
    if set(proof_by_criterion) != {criterion.criterion_id for criterion in compiled_trial.criteria}:
        raise ValueError("every compiled criterion requires one decision-time proof")
    critical = [
        criterion for criterion in compiled_trial.criteria if criterion.criticality == "CRITICAL"
    ]
    critical_proofs = [proof_by_criterion[item.criterion_id] for item in critical]
    verified_fail_count = sum(
        proof.final_verdict is CriterionVerdict.FAIL and proof.hard_decision_allowed
        for proof in critical_proofs
    )
    critical_unknown_count = sum(
        proof.final_verdict is CriterionVerdict.UNKNOWN for proof in critical_proofs
    )
    conflict_count = sum(
        proof.final_verdict is CriterionVerdict.CONFLICT for proof in critical_proofs
    )
    opaque_critical_count = sum(criterion.opaque for criterion in critical)
    has_verifier_block = any(proof.blocking_issue_codes for proof in critical_proofs)
    has_unverified_protocol = any(not criterion.protocol_verified for criterion in critical)

    if irrelevant:
        decision = TrialDecision.IRRELEVANT
    elif verified_fail_count:
        decision = TrialDecision.INELIGIBLE
    elif conflict_count or opaque_critical_count or has_verifier_block or has_unverified_protocol:
        decision = TrialDecision.REVIEW_REQUIRED
    elif critical_unknown_count:
        decision = TrialDecision.POTENTIAL_MATCH
    elif all(
        proof.final_verdict in {CriterionVerdict.PASS, CriterionVerdict.NOT_APPLICABLE}
        for proof in critical_proofs
    ):
        decision = TrialDecision.PRE_SCREEN_PASS
    else:
        decision = TrialDecision.REVIEW_REQUIRED

    total_weight = sum(
        2 if item.criticality == "CRITICAL" else 1 for item in compiled_trial.criteria
    )
    verified_weight = sum(
        (2 if item.criticality == "CRITICAL" else 1)
        for item in compiled_trial.criteria
        if _proof_is_complete(proof_by_criterion[item.criterion_id])
    )
    proof_completeness = verified_weight / total_weight if total_weight else 0.0
    resolved = sum(
        proof.final_verdict in {CriterionVerdict.PASS, CriterionVerdict.FAIL}
        for proof in critical_proofs
    )
    passed = sum(proof.final_verdict is CriterionVerdict.PASS for proof in critical_proofs)
    pass_ratio = passed / resolved if resolved else 0.0
    display_score = round(
        max(
            0.0,
            min(
                100.0,
                100
                * (
                    0.40 * pass_ratio
                    + 0.25 * proof_completeness
                    + 0.25 * retrieval_score
                    + 0.10 * _STATUS_SCORE.get(raw_trial.overall_status, 0.0)
                ),
            ),
        )
    )
    update_date = raw_trial.last_update_post_date or date(1970, 1, 1)
    epoch_days = (update_date - date(1970, 1, 1)).days
    ranking_key = RankingKey(
        tier_order=_TIER_ORDER[decision],
        verified_fail_count=verified_fail_count,
        critical_unknown_count=critical_unknown_count,
        proof_completeness=Decimal(str(proof_completeness)).quantize(Decimal("0.00000001")),
        retrieval_score=Decimal(str(retrieval_score)).quantize(Decimal("0.00000001")),
        recruitment_status_priority=_STATUS_PRIORITY.get(raw_trial.overall_status, 0),
        last_update_epoch_days=epoch_days,
        nct_id=raw_trial.nct_id,
    )
    return TrialEvaluation(
        session_id=session_id,
        patient_state_version=patient_state_version,
        nct_id=raw_trial.nct_id,
        criterion_proof_ids=[
            proof_by_criterion[item.criterion_id].proof_id for item in compiled_trial.criteria
        ],
        decision=decision,
        retrieval_score=retrieval_score,
        proof_completeness=proof_completeness,
        critical_unknown_count=critical_unknown_count,
        verified_fail_count=verified_fail_count,
        conflict_count=conflict_count,
        opaque_critical_count=opaque_critical_count,
        ranking_key=ranking_key,
        display_score=float(display_score),
        degradation_codes=[],
    )
