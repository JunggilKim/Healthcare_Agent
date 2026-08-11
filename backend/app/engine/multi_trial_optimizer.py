from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.enums import (
    AcquisitionAction,
    CriterionVerdict,
    EvidenceGrade,
    TrialDecision,
)
from backend.app.domain.evidence import PatientFact, SourceSpan
from backend.app.domain.proof import ProofPacket
from backend.app.domain.questions import (
    AffectedCriterion,
    AnswerBranch,
    BranchMetrics,
    QuestionCandidate,
    QuestionSelection,
    UtilityComponents,
)
from backend.app.domain.sessions import SessionAggregate
from backend.app.domain.trials import ProtocolReviewArtifact, RawTrialRecord
from backend.app.engine.branch_builder import build_branches, deterministic_question_id
from backend.app.engine.incremental import reevaluate_for_answered_slot

_BURDEN = {
    "boolean_patient_known": 0.03,
    "categorical_patient_known": 0.05,
    "numeric_or_date": 0.06,
    "request_record": 0.12,
    "clinician_review": 0.18,
}
_SENSITIVITY = {"ordinary": 0.0, "moderate": 0.03, "high": 0.07}
_UNRESOLVED = {TrialDecision.POTENTIAL_MATCH, TrialDecision.REVIEW_REQUIRED}
_TERMINAL = {TrialDecision.PRE_SCREEN_PASS, TrialDecision.INELIGIBLE}


@dataclass(slots=True)
class FullOptimizationState:
    aggregate: SessionAggregate
    proofs_by_trial: dict[str, list[ProofPacket]]
    raw_trials: dict[str, RawTrialRecord]
    reviews: dict[str, ProtocolReviewArtifact]
    registry_data_versions: dict[str, str | None]
    source_texts: dict[str, str]
    slots: dict[str, SlotDefinition]
    evaluated_at: datetime
    dependency_stop_reason: str | None = None
    recompiled_trial_ids: list[str] = field(default_factory=list)

    def deep_copy_for_simulation(self) -> FullOptimizationState:
        return copy.deepcopy(self)


@dataclass(frozen=True)
class _SimulatedCandidate:
    candidate: QuestionCandidate
    metrics: list[BranchMetrics]
    outcomes: list[dict[str, tuple[int, TrialDecision]]]
    raw_coverage: float


@dataclass(frozen=True)
class CandidatePolicyStatistics:
    """Shared counterfactual statistics used by transparent benchmark baselines."""

    candidate: QuestionCandidate
    raw_coverage: float
    mean_verified_trial_eliminations: float


def rank_discount(rank: int) -> float:
    return 1.0 / math.log2(rank + 1)


def compute_topk_risk(state: FullOptimizationState) -> float:
    aggregate = state.aggregate
    top_ids = aggregate.ranked_nct_ids[: aggregate.config.top_k]
    if not top_ids:
        return 0.0
    numerator = 0.0
    denominator = 0.0
    for rank, nct_id in enumerate(top_ids, start=1):
        compiled = aggregate.compiled_trials[nct_id]
        critical_ids = {
            criterion.criterion_id
            for criterion in compiled.criteria
            if criterion.criticality == "CRITICAL"
        }
        critical_proofs = [
            proof for proof in state.proofs_by_trial[nct_id] if proof.criterion_id in critical_ids
        ]
        unknown_ratio = (
            sum(proof.final_verdict is CriterionVerdict.UNKNOWN for proof in critical_proofs)
            / len(critical_proofs)
            if critical_proofs
            else 0.0
        )
        evaluation = aggregate.trial_evaluations[nct_id]
        conflict_ratio = min(1.0, evaluation.conflict_count / 2)
        proof_gap = 1.0 - evaluation.proof_completeness
        trial_risk = 0.55 * unknown_ratio + 0.25 * conflict_ratio + 0.20 * proof_gap
        discount = rank_discount(rank)
        numerator += discount * trial_risk
        denominator += discount
    return numerator / denominator


def generate_slot_candidates(state: FullOptimizationState) -> list[QuestionCandidate]:
    aggregate = state.aggregate
    rank_by_trial = {nct_id: rank for rank, nct_id in enumerate(aggregate.ranked_nct_ids, start=1)}
    affected_by_slot: dict[str, list[AffectedCriterion]] = {}
    top_ids = set(aggregate.ranked_nct_ids[: aggregate.config.top_k])
    for nct_id in top_ids:
        proof_by_criterion = {
            proof.criterion_id: proof for proof in state.proofs_by_trial.get(nct_id, [])
        }
        for criterion in aggregate.compiled_trials[nct_id].criteria:
            proof = proof_by_criterion.get(criterion.criterion_id)
            if (
                proof is None
                or criterion.criticality != "CRITICAL"
                or proof.final_verdict not in {CriterionVerdict.UNKNOWN, CriterionVerdict.CONFLICT}
            ):
                continue
            unresolved_slots = set(proof.missing_slot_ids)
            if proof.final_verdict is CriterionVerdict.CONFLICT:
                unresolved_slots.update(criterion.required_slots)
            for slot_id in unresolved_slots:
                affected_by_slot.setdefault(slot_id, []).append(
                    AffectedCriterion(
                        nct_id=nct_id,
                        criterion_id=criterion.criterion_id,
                        current_verdict=proof.final_verdict,
                        criticality=criterion.criticality,
                        current_rank=rank_by_trial[nct_id],
                    )
                )

    candidates: list[QuestionCandidate] = []
    unavailable = set(aggregate.unavailable_slot_ids) | set(aggregate.asked_slot_ids)
    for slot_id, affected in sorted(affected_by_slot.items()):
        if slot_id in unavailable:
            continue
        slot = state.slots.get(slot_id)
        if slot is None:
            continue
        question_id = deterministic_question_id(
            aggregate.session_id, aggregate.patient_state_version, slot_id
        )
        criteria = [
            aggregate.compiled_trials[item.nct_id].criteria[
                next(
                    index
                    for index, criterion in enumerate(
                        aggregate.compiled_trials[item.nct_id].criteria
                    )
                    if criterion.criterion_id == item.criterion_id
                )
            ]
            for item in affected
        ]
        conflicted = any(
            conflict.slot_id == slot_id and conflict.status == "OPEN"
            for conflict in aggregate.conflicts
        )
        candidates.append(
            QuestionCandidate(
                question_id=question_id,
                slot_id=slot_id,
                action=AcquisitionAction(slot.default_action),
                answer_type=slot.value_type,
                affected=sorted(affected, key=lambda item: (item.current_rank, item.criterion_id)),
                branches=build_branches(
                    question_id=question_id,
                    slot=slot,
                    affected_criteria=criteria,
                    evaluation_date=aggregate.evaluation_date,
                    conflicted=conflicted,
                    max_branches=aggregate.config.max_branches,
                ),
                burden_penalty=_BURDEN[slot.burden_class],
                sensitivity_penalty=_SENSITIVITY[slot.sensitivity_class],
                utility_components=None,
            )
        )
    return candidates


def _synthetic_fact(
    state: FullOptimizationState, candidate: QuestionCandidate, branch: AnswerBranch
) -> PatientFact:
    assert branch.synthetic_value is not None
    text = branch.label
    source_id = f"simulation:{candidate.question_id}:{branch.branch_id}"
    state.source_texts[source_id] = text
    digest = hashlib.sha256(text.encode()).hexdigest()
    return PatientFact(
        fact_id=f"fact_sim_{hashlib.sha256(source_id.encode()).hexdigest()[:24]}",
        slot_id=candidate.slot_id,
        value=branch.synthetic_value,
        grade=EvidenceGrade.A_DIRECT,
        source_spans=[
            SourceSpan(
                source_id=source_id,
                start=0,
                end=len(text),
                quote=text,
                sha256=digest,
                language="en",
            )
        ],
        asserted_at=state.evaluated_at.astimezone(UTC),
        effective_date=state.aggregate.evaluation_date,
        admissible_for_hard_decision=True,
    )


def _apply_simulated_branch(
    state: FullOptimizationState,
    candidate: QuestionCandidate,
    branch: AnswerBranch,
) -> list[str]:
    aggregate = state.aggregate
    facts = list(aggregate.facts)
    conflicts = list(aggregate.conflicts)
    unavailable = list(aggregate.unavailable_slot_ids)
    fact_ids: list[str] = []
    if branch.response_kind == "VALUE":
        fact = _synthetic_fact(state, candidate, branch)
        facts = [item for item in facts if item.slot_id != candidate.slot_id] + [fact]
        fact_ids = [fact.fact_id]
        conflicts = [
            conflict.model_copy(update={"status": "RESOLVED", "resolution_fact_id": fact.fact_id})
            if conflict.slot_id == candidate.slot_id and conflict.status == "OPEN"
            else conflict
            for conflict in conflicts
        ]
    elif branch.response_kind in {"RETAIN_A", "RETAIN_B"}:
        matching = [
            conflict
            for conflict in conflicts
            if conflict.slot_id == candidate.slot_id and conflict.status == "OPEN"
        ]
        if matching:
            index = 0 if branch.response_kind == "RETAIN_A" else 1
            retained_id = matching[0].fact_ids[index]
            retained = next(item for item in facts if item.fact_id == retained_id)
            facts = [item for item in facts if item.slot_id != candidate.slot_id] + [retained]
            fact_ids = [retained_id]
            conflicts = [
                conflict.model_copy(
                    update={"status": "RESOLVED", "resolution_fact_id": retained_id}
                )
                if conflict.slot_id == candidate.slot_id and conflict.status == "OPEN"
                else conflict
                for conflict in conflicts
            ]
    else:
        unavailable.append(candidate.slot_id)

    result = reevaluate_for_answered_slot(
        aggregate=aggregate,
        answered_slot_id=candidate.slot_id,
        updated_facts=facts,
        updated_conflicts=conflicts,
        answer_fact_ids=fact_ids,
        proofs_by_trial=state.proofs_by_trial,
        raw_trials=state.raw_trials,
        reviews=state.reviews,
        registry_data_versions=state.registry_data_versions,
        source_texts=state.source_texts,
        slots=state.slots,
        evaluated_at=state.evaluated_at,
    )
    state.aggregate = result.aggregate.model_copy(
        update={"unavailable_slot_ids": sorted(set(unavailable))}
    )
    state.proofs_by_trial = result.proofs_by_trial
    state.recompiled_trial_ids.extend(result.recompiled_trial_ids)
    return result.changed_criterion_ids


def normalized_risk_reduction(before: float, after: float) -> float:
    return max(0.0, (before - after) / max(before, 1e-6))


def decision_resolution(before: SessionAggregate, after: SessionAggregate) -> float:
    numerator = 0.0
    denominator = 0.0
    for rank, nct_id in enumerate(before.ranked_nct_ids[: before.config.top_k], start=1):
        before_decision = before.trial_evaluations[nct_id].decision
        if before_decision not in _UNRESOLVED:
            continue
        discount = rank_discount(rank)
        denominator += discount
        after_evaluation = after.trial_evaluations.get(nct_id)
        if after_evaluation is not None and after_evaluation.decision in _TERMINAL:
            numerator += discount
    return numerator / (denominator + 1e-6)


def topk_outcome(state: SessionAggregate) -> dict[str, tuple[int, TrialDecision]]:
    return {
        nct_id: (rank, state.trial_evaluations[nct_id].decision)
        for rank, nct_id in enumerate(state.ranked_nct_ids[: state.config.top_k], start=1)
    }


def branch_discrimination(
    outcomes: list[dict[str, tuple[int, TrialDecision]]], weights: list[float]
) -> float:
    numerator = 0.0
    denominator = 0.0
    for left in range(len(outcomes)):
        for right in range(left + 1, len(outcomes)):
            pair_weight = weights[left] * weights[right]
            denominator += pair_weight
            left_outcome = outcomes[left]
            right_outcome = outcomes[right]
            union = set(left_outcome) | set(right_outcome)
            similarity_numerator = 0.0
            similarity_denominator = 0.0
            for nct_id in union:
                left_item = left_outcome.get(nct_id)
                right_item = right_outcome.get(nct_id)
                left_weight = rank_discount(left_item[0]) if left_item else 0.0
                right_weight = rank_discount(right_item[0]) if right_item else 0.0
                if left_item and right_item:
                    agreement = 1.0 if left_item[1] is right_item[1] else 0.5
                else:
                    agreement = 0.0
                similarity_numerator += min(left_weight, right_weight) * agreement
                similarity_denominator += max(left_weight, right_weight)
            similarity = similarity_numerator / (similarity_denominator + 1e-6)
            numerator += pair_weight * (1.0 - similarity)
    return numerator / (denominator + 1e-6)


def _simulate_candidate(
    state: FullOptimizationState,
    candidate: QuestionCandidate,
    before_risk: float,
) -> _SimulatedCandidate:
    metrics: list[BranchMetrics] = []
    outcomes: list[dict[str, tuple[int, TrialDecision]]] = []
    for branch in candidate.branches:
        simulated = state.deep_copy_for_simulation()
        _apply_simulated_branch(simulated, candidate, branch)
        metrics.append(
            BranchMetrics(
                risk_reduction=normalized_risk_reduction(before_risk, compute_topk_risk(simulated)),
                decision_resolution=decision_resolution(state.aggregate, simulated.aggregate),
            )
        )
        outcomes.append(topk_outcome(simulated.aggregate))
    raw_coverage = sum(
        rank_discount(item.current_rank) * (2 if item.criticality == "CRITICAL" else 1)
        for item in {(item.nct_id, item.criterion_id): item for item in candidate.affected}.values()
    )
    return _SimulatedCandidate(candidate, metrics, outcomes, raw_coverage)


def candidate_policy_statistics(state: FullOptimizationState) -> list[CandidatePolicyStatistics]:
    """Evaluate candidates with the same branch simulator used by the live B6 policy."""

    before_risk = compute_topk_risk(state)
    before_decisions = {
        nct_id: evaluation.decision
        for nct_id, evaluation in state.aggregate.trial_evaluations.items()
    }
    statistics: list[CandidatePolicyStatistics] = []
    for candidate in generate_slot_candidates(state):
        simulated = _simulate_candidate(state, candidate, before_risk)
        eliminations = [
            sum(
                decision is TrialDecision.INELIGIBLE
                and before_decisions.get(nct_id) is not TrialDecision.INELIGIBLE
                for nct_id, (_, decision) in outcome.items()
            )
            for outcome in simulated.outcomes
        ]
        statistics.append(
            CandidatePolicyStatistics(
                candidate=candidate,
                raw_coverage=simulated.raw_coverage,
                mean_verified_trial_eliminations=(
                    sum(eliminations) / len(eliminations) if eliminations else 0.0
                ),
            )
        )
    return statistics


def _score(simulated: _SimulatedCandidate, coverage: float) -> QuestionCandidate:
    metrics = simulated.metrics
    candidate = simulated.candidate
    mean_risk = sum(item.risk_reduction for item in metrics) / len(metrics)
    minimum_risk = min(item.risk_reduction for item in metrics)
    mean_resolution = sum(item.decision_resolution for item in metrics) / len(metrics)
    discrimination = branch_discrimination(
        simulated.outcomes, [branch.weight for branch in candidate.branches]
    )
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


def _select_with_ties(scored: list[QuestionCandidate]) -> list[QuestionCandidate]:
    maximum = max(
        item.utility_components.final_utility  # type: ignore[union-attr]
        for item in scored
    )
    tied = [
        item
        for item in scored
        if abs(item.utility_components.final_utility - maximum) <= 1e-9  # type: ignore[union-attr]
    ]
    best = min(
        tied,
        key=lambda item: (
            item.burden_penalty,
            item.sensitivity_penalty,
            -item.utility_components.coverage,  # type: ignore[union-attr]
            item.slot_id,
        ),
    )
    remaining = [item for item in scored if item.question_id != best.question_id]
    remaining.sort(
        key=lambda item: (
            -item.utility_components.final_utility,  # type: ignore[union-attr]
            item.burden_penalty,
            item.sensitivity_penalty,
            -item.utility_components.coverage,  # type: ignore[union-attr]
            item.slot_id,
        )
    )
    return [best, *remaining]


def _stop(reason: str, candidates: list[QuestionCandidate]) -> QuestionSelection:
    messages = {
        "NO_RELEVANT_TRIALS": "현재 상세 평가할 관련 임상시험이 없습니다.",
        "NO_ACTIONABLE_MISSING_SLOT": "현재 안전하게 확인할 수 있는 추가 정보가 없습니다.",
        "UTILITY_BELOW_THRESHOLD": "추가 질문의 예상 효용이 중단 기준보다 낮습니다.",
        "MAX_QUESTION_BUDGET": "질문 예산에 도달했습니다.",
        "TOP_RESULT_STABLE": "상위 결과와 근거가 충분히 안정적입니다.",
        "ALL_RECORD_ACTIONS_DECLINED": "남은 기록 확인 요청을 모두 사용할 수 없습니다.",
        "TOP3_BRANCH_STABLE": "가능한 답변에 따라 상위 결과가 달라지지 않습니다.",
    }
    return QuestionSelection(
        selected=None,
        stop_reason=reason,
        top_alternatives=candidates[:3],
        patient_facing_question=None,
        deterministic_rationale=messages.get(reason, "안전 또는 의존성 제한으로 중단합니다."),
    )


def select_next_action(state: FullOptimizationState) -> QuestionSelection:
    aggregate = state.aggregate
    if not aggregate.ranked_nct_ids:
        return _stop("NO_RELEVANT_TRIALS", [])
    if state.dependency_stop_reason:
        return _stop(state.dependency_stop_reason, [])
    if aggregate.question_count >= aggregate.config.max_questions:
        return _stop("MAX_QUESTION_BUDGET", [])
    candidates = generate_slot_candidates(state)
    if not candidates:
        return _stop("NO_ACTIONABLE_MISSING_SLOT", [])

    before_risk = compute_topk_risk(state)
    simulated = [_simulate_candidate(state, candidate, before_risk) for candidate in candidates]
    maximum_coverage = max(item.raw_coverage for item in simulated)
    scored = [
        _score(item, item.raw_coverage / maximum_coverage if maximum_coverage else 0.0)
        for item in simulated
    ]
    ordered = _select_with_ties(scored)
    best = ordered[0]
    components = best.utility_components
    assert components is not None
    if components.final_utility < aggregate.config.stop_utility_threshold:
        return _stop("UTILITY_BELOW_THRESHOLD", ordered)

    all_top3_stable = all(
        all(
            list(outcome.items())[:3] == list(topk_outcome(aggregate).items())[:3]
            for outcome in item.outcomes
        )
        for item in simulated
    )
    top_evaluation = aggregate.trial_evaluations[aggregate.ranked_nct_ids[0]]
    if (
        top_evaluation.decision is TrialDecision.PRE_SCREEN_PASS
        and top_evaluation.proof_completeness == 1.0
        and all_top3_stable
    ):
        return _stop("TOP_RESULT_STABLE", ordered)
    if all_top3_stable and all(
        sum(metric.risk_reduction for metric in item.metrics) / len(item.metrics)
        < aggregate.config.stable_risk_reduction_threshold
        for item in simulated
    ):
        return _stop("TOP3_BRANCH_STABLE", ordered)

    trial_count = len({item.nct_id for item in best.affected})
    criterion_count = len({(item.nct_id, item.criterion_id) for item in best.affected})
    rationale = (
        f"현재 상위 5개 임상시험 중 {trial_count}개의 미확인 조건 {criterion_count}개에 영향을 "
        f"주며, 답변 후 판정 불확실성이 약 "
        f"{round(components.mean_risk_reduction * 100)}% 감소할 것으로 계산되어 먼저 선택했습니다."
    )
    return QuestionSelection(
        selected=best,
        stop_reason=None,
        top_alternatives=ordered[:3],
        patient_facing_question=state.slots[best.slot_id].question_template_ko,
        deterministic_rationale=rationale,
    )
