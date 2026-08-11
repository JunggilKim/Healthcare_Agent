from __future__ import annotations

import random
import statistics
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from backend.app.application.catalog import load_slot_catalog
from backend.app.domain.canonical import canonical_sha256, load_yaml
from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.questions import OptimizerRuntimeConfig, QuestionCandidate
from backend.app.domain.sessions import SessionAggregate
from backend.app.engine.incremental import reevaluate_for_answered_slot
from backend.app.engine.multi_trial_optimizer import (
    CandidatePolicyStatistics,
    FullOptimizationState,
    candidate_policy_statistics,
    select_next_action,
)
from backend.app.engine.proof_verifier import build_verified_proof
from backend.app.engine.ranker import rank_trials
from backend.app.engine.trial_aggregator import aggregate_trial, is_trial_irrelevant
from backend.app.evaluation.corpus import ReleaseCorpus
from backend.app.evaluation.execution import (
    benchmark_fact_source_texts,
    eligibility_context_from_world,
)
from backend.app.evaluation.metrics import mean, median
from backend.app.evaluation.models import (
    BenchmarkArtifact,
    MissingnessObservation,
    PatientWorld,
    WorldFact,
)
from backend.app.evaluation.policy_evidence import (
    DirectLLMChoiceStep,
    DirectLLMPolicyEvidence,
)
from backend.app.evaluation.retrieval_evidence import CuratedRetrievalEvidence
from backend.app.settings import REPOSITORY_ROOT

QuestionPolicy = Literal["B0", "B1", "B2", "B3", "B4", "B5", "B6"]


def _optimizer_config(max_questions: int) -> OptimizerRuntimeConfig:
    payload = load_yaml(REPOSITORY_ROOT / "config" / "question_optimizer.yaml")
    hard_max = int(payload["hard_max_questions"])
    if max_questions < 0 or max_questions > hard_max:
        raise ValueError("BENCHMARK_QUESTION_BUDGET_OUT_OF_RANGE")
    return OptimizerRuntimeConfig(
        top_k=int(payload["top_k"]),
        max_questions=max_questions,
        hard_max_questions=hard_max,
        max_branches=int(payload["max_branches"]),
        stop_utility_threshold=float(payload["stop_utility_threshold"]),
        stable_risk_reduction_threshold=float(payload["stable_risk_reduction_threshold"]),
    )


def _visible_world_facts(
    world: PatientWorld, observation: MissingnessObservation | None
) -> list[WorldFact]:
    if observation is None:
        return list(world.facts)
    visible_ids = set(observation.visible_fact_ids)
    return [fact for fact in world.facts if fact.fact_id in visible_ids]


def _visible_conflict_slots(world: PatientWorld, facts: list[WorldFact]) -> list[str]:
    counts: dict[str, int] = {}
    for fact in facts:
        counts[fact.slot_id] = counts.get(fact.slot_id, 0) + 1
    return [slot_id for slot_id in world.conflict_slots if counts.get(slot_id, 0) >= 2]


def build_optimization_state(
    *,
    world: PatientWorld,
    observation: MissingnessObservation | None,
    corpus: ReleaseCorpus,
    retrieval_scores: dict[str, float],
    exact_condition_matches: dict[str, bool],
    detailed_nct_ids: list[str],
    max_questions: int,
    run_key: str,
) -> FullOptimizationState:
    if observation is not None and (
        observation.world_id != world.world_id or observation.nct_id != world.nct_id
    ):
        raise ValueError("BENCHMARK_OBSERVATION_WORLD_MISMATCH")
    if not detailed_nct_ids or len(detailed_nct_ids) > 8:
        raise ValueError("BENCHMARK_DETAILED_TRIAL_COUNT_INVALID")
    if len(set(detailed_nct_ids)) != len(detailed_nct_ids):
        raise ValueError("BENCHMARK_DETAILED_TRIAL_DUPLICATE")
    missing_trials = set(detailed_nct_ids) - set(corpus.compiled_trials)
    if missing_trials:
        raise ValueError(f"BENCHMARK_DETAILED_TRIAL_MISSING:{sorted(missing_trials)}")
    if set(detailed_nct_ids) - set(retrieval_scores):
        raise ValueError("BENCHMARK_RETRIEVAL_SCORE_MISSING")
    if set(detailed_nct_ids) - set(exact_condition_matches):
        raise ValueError("BENCHMARK_EXACT_CONDITION_MATCH_MISSING")

    session_id = str(uuid5(NAMESPACE_URL, f"trial-opt-benchmark:{run_key}"))
    evaluated_at = datetime.combine(world.evaluation_date, datetime.min.time(), tzinfo=UTC)
    slots = load_slot_catalog().by_id()
    visible_facts = _visible_world_facts(world, observation)
    conflict_slots = _visible_conflict_slots(world, visible_facts)
    context = eligibility_context_from_world(
        visible_facts,
        conflict_slots,
        evaluation_date=world.evaluation_date,
        language=world.narrative_language,
    )
    source_texts = {
        **corpus.source_texts,
        **benchmark_fact_source_texts(list(world.facts)),
    }
    proofs_by_trial = {}
    evaluations = []
    compiled_trials = {}
    raw_trials = {}
    reviews = {}
    registry_versions: dict[str, str | None] = {}
    for nct_id in detailed_nct_ids:
        compiled = corpus.compiled_trials[nct_id]
        raw = corpus.raw_trials[nct_id]
        review = corpus.reviews[nct_id]
        packets = [
            build_verified_proof(
                session_id=session_id,
                patient_state_version=0,
                evaluation_date=world.evaluation_date,
                criterion=criterion,
                compiled_trial=compiled,
                review=review,
                raw_trial=raw,
                registry_data_version=raw.version_holder,
                eligibility_context=context,
                source_texts=source_texts,
                slots=slots,
                evaluated_at=evaluated_at,
            )
            for criterion in compiled.criteria
        ]
        proofs_by_trial[nct_id] = packets
        evaluations.append(
            aggregate_trial(
                session_id=session_id,
                patient_state_version=0,
                compiled_trial=compiled,
                raw_trial=raw,
                proofs=packets,
                retrieval_score=retrieval_scores[nct_id],
                irrelevant=is_trial_irrelevant(
                    retrieval_score=retrieval_scores[nct_id],
                    exact_condition_match=exact_condition_matches[nct_id],
                    compiled_trial=compiled,
                    facts=context.facts,
                ),
            )
        )
        compiled_trials[nct_id] = compiled
        raw_trials[nct_id] = raw
        reviews[nct_id] = review
        registry_versions[nct_id] = raw.version_holder
    ranked = rank_trials(evaluations)
    aggregate = SessionAggregate(
        session_id=session_id,
        mode="snapshot",
        evaluation_date=world.evaluation_date,
        patient_state_version=0,
        question_count=0,
        facts=context.facts,
        retrieval_hypotheses=[],
        conflicts=context.conflicts,
        compiled_trials=compiled_trials,
        trial_evaluations={item.nct_id: item for item in evaluations},
        ranked_nct_ids=[item.nct_id for item in ranked],
        asked_slot_ids=[],
        unavailable_slot_ids=[],
        current_question_id=None,
        config=_optimizer_config(max_questions),
    )
    return FullOptimizationState(
        aggregate=aggregate,
        proofs_by_trial=proofs_by_trial,
        raw_trials=raw_trials,
        reviews=reviews,
        registry_data_versions=registry_versions,
        source_texts=source_texts,
        slots=slots,
        evaluated_at=evaluated_at,
    )


def _top3_signature(aggregate: SessionAggregate) -> list[tuple[str, str]]:
    return [
        (nct_id, aggregate.trial_evaluations[nct_id].decision.value)
        for nct_id in aggregate.ranked_nct_ids[:3]
    ]


def _decision_accuracy(aggregate: SessionAggregate, target: SessionAggregate) -> float:
    nct_ids = set(target.trial_evaluations)
    return mean(
        [
            float(
                aggregate.trial_evaluations[nct_id].decision
                is target.trial_evaluations[nct_id].decision
            )
            for nct_id in nct_ids
        ]
    )


def _unresolved_critical_count(state: FullOptimizationState) -> int:
    top_ids = state.aggregate.ranked_nct_ids[: state.aggregate.config.top_k]
    critical = {
        (nct_id, criterion.criterion_id)
        for nct_id in top_ids
        for criterion in state.aggregate.compiled_trials[nct_id].criteria
        if criterion.criticality == "CRITICAL"
    }
    return sum(
        proof.final_verdict in {CriterionVerdict.UNKNOWN, CriterionVerdict.CONFLICT}
        for nct_id in top_ids
        for proof in state.proofs_by_trial[nct_id]
        if (nct_id, proof.criterion_id) in critical
    )


def _choose_baseline_candidate(
    policy: QuestionPolicy,
    statistics_rows: list[CandidatePolicyStatistics],
    *,
    rng: random.Random,
    direct_llm_slot: str | None,
) -> QuestionCandidate | None:
    if not statistics_rows:
        return None
    if policy == "B1":
        return min(statistics_rows, key=lambda item: item.candidate.slot_id).candidate
    if policy == "B2":
        return rng.choice(statistics_rows).candidate
    if policy == "B3":
        return min(
            statistics_rows,
            key=lambda item: (
                -item.raw_coverage,
                item.candidate.burden_penalty,
                item.candidate.slot_id,
            ),
        ).candidate
    if policy == "B4":
        return min(
            statistics_rows,
            key=lambda item: (
                -(item.mean_verified_trial_eliminations - item.candidate.burden_penalty),
                item.candidate.burden_penalty,
                item.candidate.slot_id,
            ),
        ).candidate
    if policy == "B5":
        if direct_llm_slot is None:
            raise ValueError("B5_DIRECT_LLM_CHOICE_MISSING")
        matching = [
            item.candidate for item in statistics_rows if item.candidate.slot_id == direct_llm_slot
        ]
        if not matching:
            raise ValueError("B5_DIRECT_LLM_CHOICE_OUTSIDE_CANDIDATE_LIST")
        return matching[0]
    raise ValueError(f"UNSUPPORTED_BASELINE_POLICY:{policy}")


def _apply_oracle_answer(
    state: FullOptimizationState,
    candidate: QuestionCandidate,
    *,
    world: PatientWorld,
    observation: MissingnessObservation,
) -> int:
    oracle = {item.slot_id: item for item in observation.oracle}
    answer = oracle.get(candidate.slot_id)
    slot_facts = [fact for fact in world.facts if fact.slot_id == candidate.slot_id]
    unavailable = answer is None or answer.unknown or not slot_facts
    current_facts = [fact for fact in state.aggregate.facts if fact.slot_id != candidate.slot_id]
    current_conflicts = [
        conflict for conflict in state.aggregate.conflicts if conflict.slot_id != candidate.slot_id
    ]
    answer_fact_ids: list[str] = []
    unavailable_slots = set(state.aggregate.unavailable_slot_ids)
    if unavailable:
        unavailable_slots.add(candidate.slot_id)
    else:
        revealed = eligibility_context_from_world(
            slot_facts,
            [candidate.slot_id]
            if candidate.slot_id in world.conflict_slots and len(slot_facts) >= 2
            else [],
            evaluation_date=world.evaluation_date,
            language=world.narrative_language,
        )
        current_facts.extend(revealed.facts)
        current_conflicts.extend(revealed.conflicts)
        answer_fact_ids = [fact.fact_id for fact in revealed.facts]
        unavailable_slots.discard(candidate.slot_id)

    before_unresolved = _unresolved_critical_count(state)
    result = reevaluate_for_answered_slot(
        aggregate=state.aggregate,
        answered_slot_id=candidate.slot_id,
        updated_facts=current_facts,
        updated_conflicts=current_conflicts,
        answer_fact_ids=answer_fact_ids,
        proofs_by_trial=state.proofs_by_trial,
        raw_trials=state.raw_trials,
        reviews=state.reviews,
        registry_data_versions=state.registry_data_versions,
        source_texts=state.source_texts,
        slots=state.slots,
        evaluated_at=state.evaluated_at,
    )
    state.aggregate = result.aggregate.model_copy(
        update={
            "question_count": state.aggregate.question_count + 1,
            "asked_slot_ids": sorted({*state.aggregate.asked_slot_ids, candidate.slot_id}),
            "unavailable_slot_ids": sorted(unavailable_slots),
            "current_question_id": None,
        }
    )
    state.proofs_by_trial = result.proofs_by_trial
    state.recompiled_trial_ids.extend(result.recompiled_trial_ids)
    return max(0, before_unresolved - _unresolved_critical_count(state))


def run_question_policy(
    *,
    policy: QuestionPolicy,
    world: PatientWorld,
    observation: MissingnessObservation,
    corpus: ReleaseCorpus,
    retrieval_scores: dict[str, float],
    exact_condition_matches: dict[str, bool],
    detailed_nct_ids: list[str],
    seed: int,
    max_questions: int,
    direct_llm_steps: list[DirectLLMChoiceStep] | None = None,
) -> dict[str, Any]:
    run_key = f"{observation.observation_id}:{policy}:{seed}"
    state = build_optimization_state(
        world=world,
        observation=observation,
        corpus=corpus,
        retrieval_scores=retrieval_scores,
        exact_condition_matches=exact_condition_matches,
        detailed_nct_ids=detailed_nct_ids,
        max_questions=max_questions,
        run_key=run_key,
    )
    target = build_optimization_state(
        world=world,
        observation=None,
        corpus=corpus,
        retrieval_scores=retrieval_scores,
        exact_condition_matches=exact_condition_matches,
        detailed_nct_ids=detailed_nct_ids,
        max_questions=max_questions,
        run_key=f"{run_key}:complete",
    )
    rng = random.Random(seed)
    target_signature = _top3_signature(target.aggregate)
    accuracy_curve: list[float] = []
    top3_curve: list[float] = []
    asked_slots: list[str] = []
    resolved_counts: list[int] = []
    burden_costs: list[float] = []
    stop_reason: str | None = None
    stable_streak = 0
    stable_at: int | None = None

    for question_index in range(max_questions + 1):
        accuracy_curve.append(_decision_accuracy(state.aggregate, target.aggregate))
        agreement = _top3_signature(state.aggregate) == target_signature
        top3_curve.append(float(agreement))
        stable_streak = stable_streak + 1 if agreement else 0
        if stable_streak >= 2 and stable_at is None:
            stable_at = question_index
        if question_index == max_questions or policy == "B0":
            stop_reason = (
                "MAX_QUESTION_BUDGET" if question_index == max_questions else "B0_NO_QUESTION"
            )
            break

        if policy == "B6":
            selection = select_next_action(state)
            candidate = selection.selected
            if candidate is None:
                stop_reason = selection.stop_reason
                if agreement and stable_at is None:
                    stable_at = question_index
                break
        else:
            statistics_rows = candidate_policy_statistics(state)
            direct_slot = (
                direct_llm_steps[question_index].selected_slot_id
                if direct_llm_steps is not None and question_index < len(direct_llm_steps)
                else None
            )
            if policy == "B5" and direct_llm_steps is not None:
                if question_index >= len(direct_llm_steps):
                    raise ValueError("B5_DIRECT_LLM_CHOICE_MISSING")
                actual_candidates = sorted(item.candidate.slot_id for item in statistics_rows)
                if sorted(direct_llm_steps[question_index].candidate_slot_ids) != (
                    actual_candidates
                ):
                    raise ValueError("B5_RECORDED_CANDIDATE_LIST_MISMATCH")
            candidate = _choose_baseline_candidate(
                policy,
                statistics_rows,
                rng=rng,
                direct_llm_slot=direct_slot,
            )
            if candidate is None:
                stop_reason = "NO_ACTIONABLE_MISSING_SLOT"
                if agreement and stable_at is None:
                    stable_at = question_index
                break
        asked_slots.append(candidate.slot_id)
        burden_costs.append(candidate.burden_penalty + candidate.sensitivity_penalty)
        resolved_counts.append(
            _apply_oracle_answer(
                state,
                candidate,
                world=world,
                observation=observation,
            )
        )

    while len(accuracy_curve) < max_questions + 1:
        accuracy_curve.append(accuracy_curve[-1])
        top3_curve.append(top3_curve[-1])
    questions_to_decision = next(
        (index for index, value in enumerate(accuracy_curve) if value == 1.0),
        max_questions + 1,
    )
    return {
        "policy": policy,
        "observation_id": observation.observation_id,
        "world_id": world.world_id,
        "nct_id": world.nct_id,
        "split": observation.split,
        "rate": observation.rate,
        "pattern": observation.pattern,
        "questions": len(asked_slots),
        "questions_to_decision": questions_to_decision,
        "stable_top3_questions": stable_at if stable_at is not None else max_questions + 1,
        "accuracy_curve": accuracy_curve,
        "top3_agreement_curve": top3_curve,
        "final_accuracy": accuracy_curve[-1],
        "final_top3_agreement": top3_curve[-1],
        "target_top3": target_signature,
        "final_top3": _top3_signature(state.aggregate),
        "asked_slots": asked_slots,
        "resolved_critical_per_question": resolved_counts,
        "burden_costs": burden_costs,
        "stop_reason": stop_reason,
        "recompiled_trial_ids": state.recompiled_trial_ids,
    }


def summarize_question_policy(
    policy: str, rows: list[dict[str, Any]], max_questions: int
) -> dict[str, Any]:
    if not rows:
        raise ValueError(f"BENCHMARK_POLICY_ROWS_EMPTY:{policy}")
    accuracy = [
        mean([float(row["accuracy_curve"][index]) for row in rows])
        for index in range(max_questions + 1)
    ]
    top3 = [
        mean([float(row["top3_agreement_curve"][index]) for row in rows])
        for index in range(max_questions + 1)
    ]
    auc = (
        sum((accuracy[index] + accuracy[index + 1]) / 2 for index in range(max_questions))
        / max_questions
        if max_questions
        else accuracy[0]
    )
    question_counts = [float(row["questions"]) for row in rows]
    resolved = [float(value) for row in rows for value in row["resolved_critical_per_question"]]
    burdens = [float(value) for row in rows for value in row["burden_costs"]]
    return {
        "policy": policy,
        "runs": len(rows),
        "accuracy_by_question": [
            {"questions": index, "accuracy": value} for index, value in enumerate(accuracy)
        ],
        "stable_top3_agreement_by_question": [
            {"questions": index, "agreement": value} for index, value in enumerate(top3)
        ],
        "accuracy_auc": auc,
        "median_questions_to_decision": median(
            [float(row["questions_to_decision"]) for row in rows]
        ),
        "median_questions_to_stable_top3": median(
            [float(row["stable_top3_questions"]) for row in rows]
        ),
        "final_decision_accuracy": mean([float(row["final_accuracy"]) for row in rows]),
        "final_stable_top3_agreement": mean([float(row["final_top3_agreement"]) for row in rows]),
        "question_count_mean": mean(question_counts),
        "question_count_std": statistics.pstdev(question_counts),
        "resolved_critical_criteria_per_question": mean(resolved) if resolved else 0.0,
        "mean_burden_cost_per_question": mean(burdens) if burdens else 0.0,
        "max_questions_observed": int(max(question_counts)),
        "no_recompilation": not any(row["recompiled_trial_ids"] for row in rows),
    }


def _paired_bootstrap_tie(
    b3_rows: list[dict[str, Any]],
    b6_rows: list[dict[str, Any]],
    *,
    seed: int,
    samples: int = 10_000,
) -> dict[str, Any]:
    b3 = {row["observation_id"]: float(row["questions"]) for row in b3_rows}
    b6 = {row["observation_id"]: float(row["questions"]) for row in b6_rows}
    if set(b3) != set(b6) or not b3:
        raise ValueError("BENCHMARK_PAIRED_POLICY_ROWS_MISMATCH")
    differences = [b6[key] - b3[key] for key in sorted(b3)]
    rng = random.Random(seed)
    means = sorted(mean([rng.choice(differences) for _ in differences]) for _ in range(samples))
    lower = means[int(samples * 0.025)]
    upper = means[min(samples - 1, int(samples * 0.975))]
    return {
        "method": "paired bootstrap 95% CI over per-observation question-count differences",
        "samples": samples,
        "mean_difference_b6_minus_b3": mean(differences),
        "confidence_interval_95": [lower, upper],
        "statistically_tied": lower <= 0 <= upper,
    }


def evaluate_interactive_benchmark(
    *,
    benchmark: BenchmarkArtifact,
    corpus: ReleaseCorpus,
    retrieval_evidence: CuratedRetrievalEvidence,
    seed: int,
    max_questions: int,
    direct_llm_evidence: DirectLLMPolicyEvidence | None = None,
) -> dict[str, Any]:
    selected = [
        item
        for item in benchmark.observations
        if item.split == "test" and item.rate == 0.4 and item.pattern == "REALISTIC"
    ]
    if not selected:
        raise ValueError("BENCHMARK_INTERACTIVE_TEST_OBSERVATIONS_MISSING")
    worlds = {item.world_id: item for item in benchmark.worlds}
    retrieval_by_world = {item.world_id: item for item in retrieval_evidence.queries}
    if {item.world_id for item in selected} - set(retrieval_by_world):
        raise ValueError("BENCHMARK_INTERACTIVE_RETRIEVAL_EVIDENCE_MISSING")
    direct_runs = (
        {item.observation_id: item for item in direct_llm_evidence.runs}
        if direct_llm_evidence is not None
        else None
    )
    if direct_runs is not None and set(direct_runs) != {item.observation_id for item in selected}:
        raise ValueError("B5_DIRECT_LLM_OBSERVATION_COVERAGE_MISMATCH")
    if direct_llm_evidence is not None and direct_llm_evidence.random_seed != seed:
        raise ValueError("B5_DIRECT_LLM_SEED_MISMATCH")

    policies: list[QuestionPolicy] = ["B0", "B1", "B2", "B3", "B4", "B6"]
    if direct_runs is not None:
        policies.insert(5, "B5")
    summaries: dict[str, Any] = {}
    rows_by_policy: dict[str, list[dict[str, Any]]] = {}
    predictions: list[dict[str, Any]] = []
    for policy in policies:
        rows = []
        repetitions = range(10) if policy == "B2" else range(1)
        for repetition in repetitions:
            for observation in selected:
                retrieval = retrieval_by_world[observation.world_id]
                row = run_question_policy(
                    policy=policy,
                    world=worlds[observation.world_id],
                    observation=observation,
                    corpus=corpus,
                    retrieval_scores=retrieval.full_rrf_scores,
                    exact_condition_matches=retrieval.exact_condition_matches,
                    detailed_nct_ids=retrieval.detailed_nct_ids,
                    seed=seed + repetition,
                    max_questions=max_questions,
                    direct_llm_steps=(
                        direct_runs[observation.observation_id].steps
                        if policy == "B5" and direct_runs is not None
                        else None
                    ),
                )
                if (
                    policy == "B5"
                    and direct_runs is not None
                    and row["questions"] != len(direct_runs[observation.observation_id].steps)
                ):
                    raise ValueError("B5_RECORDED_STEP_COUNT_MISMATCH")
                rows.append(row)
                predictions.append({"suite": "interactive", **row})
        rows_by_policy[policy] = rows
        summaries[policy] = summarize_question_policy(policy, rows, max_questions)
    if direct_runs is None:
        summaries["B5"] = {
            "policy": "B5",
            "status": "NOT_RUN_REQUIRES_PAID_DIRECT_LLM_BASELINE",
        }

    repeated_b6 = []
    for observation in selected:
        retrieval = retrieval_by_world[observation.world_id]
        repeated_b6.append(
            run_question_policy(
                policy="B6",
                world=worlds[observation.world_id],
                observation=observation,
                corpus=corpus,
                retrieval_scores=retrieval.full_rrf_scores,
                exact_condition_matches=retrieval.exact_condition_matches,
                detailed_nct_ids=retrieval.detailed_nct_ids,
                seed=seed,
                max_questions=max_questions,
            )
        )
    repeat_seed_identical = canonical_sha256(repeated_b6) == canonical_sha256(rows_by_policy["B6"])
    statistical_test = _paired_bootstrap_tie(rows_by_policy["B3"], rows_by_policy["B6"], seed=seed)
    b3 = summaries["B3"]
    b6 = summaries["B6"]
    reduction = (
        (b3["median_questions_to_stable_top3"] - b6["median_questions_to_stable_top3"])
        / b3["median_questions_to_stable_top3"]
        if b3["median_questions_to_stable_top3"]
        else 0.0
    )
    accuracy_index = min(3, max_questions)
    return {
        "scope": "DATASET_A_HELD_OUT_40_PERCENT_REALISTIC",
        "acceptance_eligible": direct_runs is not None,
        "limitations": (
            []
            if direct_runs is not None
            else ["B5 paid direct-LLM baseline artifact is pending and is not imputed."]
        ),
        "policies": summaries,
        "median_question_reduction_vs_b3": reduction,
        "question_count_statistical_test": statistical_test,
        "decision_accuracy_after_3": b6["accuracy_by_question"][accuracy_index]["accuracy"],
        "b3_decision_accuracy_after_3": b3["accuracy_by_question"][accuracy_index]["accuracy"],
        "repeat_seed_identical": repeat_seed_identical,
        "hard_question_budget": max_questions,
        "max_policy_questions": max(
            int(summary["max_questions_observed"])
            for summary in summaries.values()
            if "max_questions_observed" in summary
        ),
        "predictions": predictions,
    }
