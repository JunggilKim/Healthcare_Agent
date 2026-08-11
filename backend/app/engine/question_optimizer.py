from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.enums import AcquisitionAction, CriterionVerdict, TrialDecision
from backend.app.domain.evidence import EligibilityContext, FactConflict, PatientFact, SourceSpan
from backend.app.domain.proof import ProofPacket
from backend.app.domain.questions import (
    AffectedCriterion,
    AnswerBranch,
    BranchMetrics,
    QuestionCandidate,
    QuestionSelection,
    UtilityComponents,
)
from backend.app.domain.ranking import TrialEvaluation
from backend.app.domain.trials import CompiledTrial, ProtocolReviewArtifact, RawTrialRecord
from backend.app.domain.values import BooleanValue, CategoricalValue, TypedValue
from backend.app.engine.proof_verifier import build_verified_proof
from backend.app.engine.trial_aggregator import aggregate_trial

_QUESTION_IDS = {
    "pathology.histology": "q_00000000-0000-4000-8000-000000000001",
    "pathology.muscle_invasion": "q_00000000-0000-4000-8000-000000000002",
}
_BURDEN = {
    "boolean_patient_known": 0.03,
    "categorical_patient_known": 0.05,
    "numeric_or_date": 0.06,
    "request_record": 0.12,
    "clinician_review": 0.18,
}
_SENSITIVITY = {"ordinary": 0.0, "moderate": 0.03, "high": 0.07}


@dataclass(slots=True)
class OptimizationState:
    session_id: str
    patient_state_version: int
    evaluation_date: date
    facts: list[PatientFact]
    conflicts: list[FactConflict]
    source_texts: dict[str, str]
    compiled_trial: CompiledTrial
    review: ProtocolReviewArtifact
    raw_trial: RawTrialRecord
    registry_data_version: str | None
    proofs: list[ProofPacket]
    trial_evaluation: TrialEvaluation
    slots: dict[str, SlotDefinition]
    enabled_acquisition_slots: tuple[str, ...]
    unavailable_slot_ids: set[str] = field(default_factory=set)
    asked_slot_ids: list[str] = field(default_factory=list)
    question_count: int = 0
    max_questions: int = 5

    def deep_copy_for_simulation(self) -> OptimizationState:
        return copy.deepcopy(self)


def _rank_discount(rank: int) -> float:
    return 1.0 / math.log2(rank + 1)


def compute_topk_risk(state: OptimizationState) -> float:
    proofs = state.proofs
    critical_count = len(proofs)
    if critical_count == 0:
        return 0.0
    unknown_ratio = (
        sum(proof.final_verdict is CriterionVerdict.UNKNOWN for proof in proofs) / critical_count
    )
    conflict_count = sum(proof.final_verdict is CriterionVerdict.CONFLICT for proof in proofs)
    conflict_ratio = min(1.0, conflict_count / 2)
    proof_gap = 1.0 - state.trial_evaluation.proof_completeness
    return 0.55 * unknown_ratio + 0.25 * conflict_ratio + 0.20 * proof_gap


def _branches(candidate: QuestionCandidate) -> list[AnswerBranch]:
    return candidate.branches


def _build_branches(question_id: str, slot: SlotDefinition) -> list[AnswerBranch]:
    values: list[tuple[str, TypedValue | None, str]]
    if slot.value_type == "boolean":
        values = [
            ("yes", BooleanValue(kind="boolean", value=True), "VALUE"),
            ("no", BooleanValue(kind="boolean", value=False), "VALUE"),
            ("unknown_or_declined", None, "UNKNOWN"),
        ]
    elif slot.slot_id == "pathology.histology":
        values = [
            (
                "urothelial/transitional-cell carcinoma",
                CategoricalValue(
                    kind="categorical",
                    value="urothelial_carcinoma",
                    system="trial-opt-canonical-v1",
                ),
                "VALUE",
            ),
            (
                "other histology",
                CategoricalValue(kind="categorical", value="other", system=None),
                "VALUE",
            ),
            ("unknown_or_declined", None, "UNKNOWN"),
        ]
    else:
        raise ValueError(f"Phase-1 branch builder does not support slot: {slot.slot_id}")
    weight = float(Decimal(1) / Decimal(len(values)))
    return [
        AnswerBranch.model_validate(
            {
                "branch_id": f"{question_id}:{index}",
                "label": label,
                "response_kind": response_kind,
                "synthetic_value": value,
                "weight": weight,
            }
        )
        for index, (label, value, response_kind) in enumerate(values)
    ]


def generate_slot_candidates(state: OptimizationState) -> list[QuestionCandidate]:
    proof_by_id = {proof.criterion_id: proof for proof in state.proofs}
    candidates: list[QuestionCandidate] = []
    for slot_id in state.enabled_acquisition_slots:
        if slot_id in state.unavailable_slot_ids or slot_id in state.asked_slot_ids:
            continue
        affected = []
        for criterion in state.compiled_trial.criteria:
            proof = proof_by_id[criterion.criterion_id]
            if (
                criterion.criticality == "CRITICAL"
                and slot_id in proof.missing_slot_ids
                and proof.final_verdict in {CriterionVerdict.UNKNOWN, CriterionVerdict.CONFLICT}
            ):
                affected.append(
                    AffectedCriterion(
                        nct_id=criterion.nct_id,
                        criterion_id=criterion.criterion_id,
                        current_verdict=proof.final_verdict,
                        criticality=criterion.criticality,
                        current_rank=1,
                    )
                )
        if not affected:
            continue
        slot = state.slots[slot_id]
        question_id = _QUESTION_IDS.get(slot_id)
        if question_id is None:
            raise ValueError(f"Phase-1 optimizer scope contains an unexpected slot: {slot_id}")
        candidates.append(
            QuestionCandidate(
                question_id=question_id,
                slot_id=slot_id,
                action=AcquisitionAction(slot.default_action),
                answer_type=slot.value_type,
                affected=affected,
                branches=_build_branches(question_id, slot),
                burden_penalty=_BURDEN[slot.burden_class],
                sensitivity_penalty=_SENSITIVITY[slot.sensitivity_class],
                utility_components=None,
            )
        )
    return candidates


def _apply_simulated_answer(
    simulated: OptimizationState,
    candidate: QuestionCandidate,
    branch: AnswerBranch,
) -> None:
    if branch.response_kind != "VALUE":
        simulated.unavailable_slot_ids.add(candidate.slot_id)
        return
    assert branch.synthetic_value is not None
    synthetic_text = branch.label
    source_id = f"simulation:{candidate.question_id}:{branch.branch_id}"
    digest = hashlib.sha256(synthetic_text.encode("utf-8")).hexdigest()
    fact = PatientFact(
        fact_id=f"fact_sim_{candidate.question_id[2:]}_{branch.branch_id.rsplit(':', 1)[1]}",
        slot_id=candidate.slot_id,
        value=branch.synthetic_value,
        grade="A",
        source_spans=[
            SourceSpan(
                source_id=source_id,
                start=0,
                end=len(synthetic_text),
                quote=synthetic_text,
                sha256=digest,
                language="en",
            )
        ],
        asserted_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        effective_date=simulated.evaluation_date,
        admissible_for_hard_decision=True,
    )
    simulated.facts = [item for item in simulated.facts if item.slot_id != candidate.slot_id] + [
        fact
    ]
    simulated.source_texts[source_id] = synthetic_text


def _recompute(simulated: OptimizationState) -> None:
    context = EligibilityContext(facts=simulated.facts, conflicts=simulated.conflicts)
    simulated.proofs = [
        build_verified_proof(
            session_id=simulated.session_id,
            patient_state_version=simulated.patient_state_version,
            evaluation_date=simulated.evaluation_date,
            criterion=criterion,
            compiled_trial=simulated.compiled_trial,
            review=simulated.review,
            raw_trial=simulated.raw_trial,
            registry_data_version=simulated.registry_data_version,
            eligibility_context=context,
            source_texts=simulated.source_texts,
            slots=simulated.slots,
            evaluated_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        )
        for criterion in simulated.compiled_trial.criteria
    ]
    simulated.trial_evaluation = aggregate_trial(
        session_id=simulated.session_id,
        patient_state_version=simulated.patient_state_version,
        compiled_trial=simulated.compiled_trial,
        raw_trial=simulated.raw_trial,
        proofs=simulated.proofs,
        retrieval_score=1.0,
    )


def _risk_reduction(before: float, after: float) -> float:
    return max(0.0, (before - after) / max(before, 1e-6))


def _decision_resolution(before: TrialEvaluation, after: TrialEvaluation) -> float:
    unresolved = before.decision in {TrialDecision.POTENTIAL_MATCH, TrialDecision.REVIEW_REQUIRED}
    terminal = after.decision in {TrialDecision.PRE_SCREEN_PASS, TrialDecision.INELIGIBLE}
    return 1.0 if unresolved and terminal else 0.0


def _branch_discrimination(
    outcomes: list[tuple[int, TrialDecision]], weights: list[float]
) -> float:
    numerator = 0.0
    denominator = 0.0
    for left in range(len(outcomes)):
        for right in range(left + 1, len(outcomes)):
            pair_weight = weights[left] * weights[right]
            denominator += pair_weight
            left_rank, left_decision = outcomes[left]
            right_rank, right_decision = outcomes[right]
            rank_weight = min(_rank_discount(left_rank), _rank_discount(right_rank))
            max_weight = max(_rank_discount(left_rank), _rank_discount(right_rank))
            agreement = 1.0 if left_decision is right_decision else 0.5
            similarity = rank_weight * agreement / (max_weight + 1e-6)
            numerator += pair_weight * (1.0 - similarity)
    return numerator / (denominator + 1e-6)


def _score_candidate(
    state: OptimizationState,
    candidate: QuestionCandidate,
    before_risk: float,
    coverage: float,
) -> QuestionCandidate:
    metrics: list[BranchMetrics] = []
    outcomes: list[tuple[int, TrialDecision]] = []
    for branch in _branches(candidate):
        simulated = state.deep_copy_for_simulation()
        _apply_simulated_answer(simulated, candidate, branch)
        _recompute(simulated)
        metrics.append(
            BranchMetrics(
                risk_reduction=_risk_reduction(before_risk, compute_topk_risk(simulated)),
                decision_resolution=_decision_resolution(
                    state.trial_evaluation, simulated.trial_evaluation
                ),
            )
        )
        outcomes.append((1, simulated.trial_evaluation.decision))
    mean_risk = sum(item.risk_reduction for item in metrics) / len(metrics)
    minimum_risk = min(item.risk_reduction for item in metrics)
    mean_resolution = sum(item.decision_resolution for item in metrics) / len(metrics)
    discrimination = _branch_discrimination(outcomes, [item.weight for item in candidate.branches])
    base = (
        0.45 * mean_risk
        + 0.20 * minimum_risk
        + 0.15 * mean_resolution
        + 0.10 * discrimination
        + 0.10 * coverage
    )
    final = base - candidate.burden_penalty - candidate.sensitivity_penalty
    return candidate.model_copy(
        update={
            "utility_components": UtilityComponents(
                mean_risk_reduction=mean_risk,
                minimum_risk_reduction=minimum_risk,
                mean_decision_resolution=mean_resolution,
                branch_discrimination=discrimination,
                coverage=coverage,
                base_utility=base,
                burden_penalty=candidate.burden_penalty,
                sensitivity_penalty=candidate.sensitivity_penalty,
                final_utility=final,
            )
        }
    )


def select_next_action(state: OptimizationState) -> QuestionSelection:
    if state.question_count >= state.max_questions:
        return QuestionSelection(
            selected=None,
            stop_reason="MAX_QUESTION_BUDGET",
            top_alternatives=[],
            patient_facing_question=None,
            deterministic_rationale="질문 예산에 도달했습니다.",
        )
    candidates = generate_slot_candidates(state)
    if not candidates:
        return QuestionSelection(
            selected=None,
            stop_reason="NO_ACTIONABLE_MISSING_SLOT",
            top_alternatives=[],
            patient_facing_question=None,
            deterministic_rationale="현재 안전하게 확인할 수 있는 추가 정보가 없습니다.",
        )
    before_risk = compute_topk_risk(state)
    raw_coverages = [
        sum(
            _rank_discount(item.current_rank) * (2 if item.criticality == "CRITICAL" else 1)
            for item in candidate.affected
        )
        for candidate in candidates
    ]
    max_coverage = max(raw_coverages)
    scored = [
        _score_candidate(
            state,
            candidate,
            before_risk,
            raw / max_coverage if max_coverage else 0.0,
        )
        for candidate, raw in zip(candidates, raw_coverages, strict=True)
    ]
    scored.sort(
        key=lambda item: (
            -round(item.utility_components.final_utility if item.utility_components else 0.0, 9),
            item.burden_penalty,
            item.sensitivity_penalty,
            -(item.utility_components.coverage if item.utility_components else 0.0),
            item.slot_id,
        )
    )
    best = scored[0]
    assert best.utility_components is not None
    if best.utility_components.final_utility < 0.10:
        return QuestionSelection(
            selected=None,
            stop_reason="UTILITY_BELOW_THRESHOLD",
            top_alternatives=scored[:3],
            patient_facing_question=None,
            deterministic_rationale="추가 질문의 예상 효용이 중단 기준보다 낮습니다.",
        )
    trial_count = len({item.nct_id for item in best.affected})
    criterion_count = len(best.affected)
    rationale = (
        f"현재 상위 5개 임상시험 중 {trial_count}개의 미확인 조건 {criterion_count}개에 영향을 "
        f"주며, 답변 후 판정 불확실성이 약 "
        f"{round(best.utility_components.mean_risk_reduction * 100)}% 감소할 것으로 계산되어 "
        "먼저 선택했습니다."
    )
    return QuestionSelection(
        selected=best,
        stop_reason=None,
        top_alternatives=scored[:3],
        patient_facing_question=state.slots[best.slot_id].question_template_ko,
        deterministic_rationale=rationale,
    )
