from __future__ import annotations

from typing import Any

from backend.app.agents.report_renderer import deterministic_criterion_explanation
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade, TrialDecision
from backend.app.domain.evidence import EligibilityContext
from backend.app.engine.evaluator import evaluate_criterion
from backend.app.engine.proof_verifier import build_post_render_proof, build_verified_proof
from backend.app.evaluation.corpus import ReleaseCorpus
from backend.app.evaluation.interactive import build_optimization_state
from backend.app.evaluation.models import BenchmarkArtifact
from backend.app.evaluation.retrieval_evidence import CuratedRetrievalEvidence


def evaluate_safety_ablation_controls(
    *,
    benchmark: BenchmarkArtifact,
    corpus: ReleaseCorpus,
    max_questions: int,
) -> dict[str, dict[str, Any]]:
    passing_world = next(world for world in benchmark.worlds if world.world_type == "FULL_PASS")
    state = build_optimization_state(
        world=passing_world,
        observation=None,
        corpus=corpus,
        retrieval_scores={passing_world.nct_id: 1.0},
        exact_condition_matches={passing_world.nct_id: True},
        detailed_nct_ids=[passing_world.nct_id],
        max_questions=max_questions,
        run_key="safety-ablation-controls",
    )
    compiled = corpus.compiled_trials[passing_world.nct_id]
    criterion = next(
        item
        for item in compiled.criteria
        if not item.opaque
        and len(item.required_slots) == 1
        and any(fact.slot_id == item.required_slots[0] for fact in state.aggregate.facts)
    )
    source_fact = next(
        fact for fact in state.aggregate.facts if fact.slot_id == criterion.required_slots[0]
    )
    hypothesis = source_fact.model_copy(
        update={
            "fact_id": "hyp_ablation_firewall",
            "grade": EvidenceGrade.H_HYPOTHESIS,
            "admissible_for_hard_decision": False,
        }
    )
    ontology_fact = source_fact.model_copy(
        update={
            "fact_id": "fact_ablation_grade_c",
            "grade": EvidenceGrade.C_ONTOLOGY,
            "admissible_for_hard_decision": False,
        }
    )
    cases = {
        "A1": (hypothesis, corpus.reviews[passing_world.nct_id], {"PV-007"}),
        "A2": (
            source_fact,
            corpus.reviews[passing_world.nct_id].model_copy(update={"approved": False}),
            {f"PV-{index:03d}" for index in range(1, 15)},
        ),
        "A3": (ontology_fact, corpus.reviews[passing_world.nct_id], {"PV-008"}),
    }
    results: dict[str, dict[str, Any]] = {}
    for ablation_id, (fact, review, ignored_checks) in cases.items():
        proof = build_verified_proof(
            session_id=state.aggregate.session_id,
            patient_state_version=0,
            evaluation_date=passing_world.evaluation_date,
            criterion=criterion,
            compiled_trial=compiled,
            review=review,
            raw_trial=corpus.raw_trials[passing_world.nct_id],
            registry_data_version=corpus.raw_trials[passing_world.nct_id].version_holder,
            eligibility_context=EligibilityContext(facts=[fact], conflicts=[]),
            source_texts=state.source_texts,
            slots=state.slots,
            evaluated_at=state.evaluated_at,
        )
        remaining = [
            check_id for check_id in proof.blocking_issue_codes if check_id not in ignored_checks
        ]
        if ablation_id == "A3":
            counterfactual_verdict = evaluate_criterion(
                criterion,
                EligibilityContext(facts=[source_fact], conflicts=[]),
                passing_world.evaluation_date,
            ).verdict
            counterfactual_hard = counterfactual_verdict in {
                CriterionVerdict.PASS,
                CriterionVerdict.FAIL,
            }
        else:
            counterfactual_hard = (
                proof.provisional_verdict in {CriterionVerdict.PASS, CriterionVerdict.FAIL}
                and not remaining
            )
        results[ablation_id] = {
            "criterion_id": criterion.criterion_id,
            "evidence_grade": fact.grade.value,
            "full_proof_hard_decision_allowed": proof.hard_decision_allowed,
            "full_proof_blocking_checks": proof.blocking_issue_codes,
            "ignored_checks": sorted(ignored_checks),
            "counterfactual_hard_decision_allowed": counterfactual_hard,
            "remaining_blocking_checks": remaining,
        }
    return results


def evaluate_release_invariants(
    *,
    benchmark: BenchmarkArtifact,
    corpus: ReleaseCorpus,
    retrieval_evidence: CuratedRetrievalEvidence,
    max_questions: int,
) -> dict[str, Any]:
    hard_verdicts = {CriterionVerdict.PASS, CriterionVerdict.FAIL}
    proof_count = 0
    replay_successes = 0
    explanation_successes = 0
    grade_h_hard_occurrences = 0
    hard_proof_count = 0
    unsupported_hard_occurrences = 0
    opaque_hard_occurrences = 0
    world_by_id = {world.world_id: world for world in benchmark.worlds}

    for world in benchmark.worlds:
        state = build_optimization_state(
            world=world,
            observation=None,
            corpus=corpus,
            retrieval_scores={world.nct_id: 1.0},
            exact_condition_matches={world.nct_id: True},
            detailed_nct_ids=[world.nct_id],
            max_questions=max_questions,
            run_key=f"release-invariants:{world.world_id}",
        )
        fact_by_id = {fact.fact_id: fact for fact in state.aggregate.facts}
        for proof in state.proofs_by_trial[world.nct_id]:
            proof_count += 1
            replay = next(check for check in proof.verifier_checks if check.check_id == "PV-012")
            replay_successes += replay.passed
            rendered = build_post_render_proof(proof, deterministic_criterion_explanation(proof))
            explanation_successes += rendered.verifier_checks[-1].passed
            if proof.final_verdict in hard_verdicts:
                hard_proof_count += 1
                grade_h_hard_occurrences += sum(
                    fact_by_id[fact_id].grade is EvidenceGrade.H_HYPOTHESIS
                    for fact_id in proof.evidence_fact_ids
                    if fact_id in fact_by_id
                )
                unsupported_hard_occurrences += not proof.hard_decision_allowed
                criterion = next(
                    item
                    for item in corpus.compiled_trials[world.nct_id].criteria
                    if item.criterion_id == proof.criterion_id
                )
                opaque_hard_occurrences += criterion.opaque

    evaluation_date = min(world.evaluation_date for world in benchmark.worlds)
    missing_default_occurrences = sum(
        evaluate_criterion(
            criterion,
            EligibilityContext(facts=[], conflicts=[]),
            evaluation_date,
        ).verdict
        in hard_verdicts
        for trial in corpus.compiled_trials.values()
        for criterion in trial.criteria
        if not criterion.opaque
    )

    verified_fail_above_nonfail = 0
    material_opaque = 0
    material_criteria = 0
    displayed_hard_verdicts = 0
    displayed_hard_approved = 0
    for query in retrieval_evidence.queries:
        world = world_by_id[query.world_id]
        state = build_optimization_state(
            world=world,
            observation=None,
            corpus=corpus,
            retrieval_scores=query.full_rrf_scores,
            exact_condition_matches=query.exact_condition_matches,
            detailed_nct_ids=query.detailed_nct_ids,
            max_questions=max_questions,
            run_key=f"release-ranking-invariants:{query.query_id}",
        )
        ranked = state.aggregate.ranked_nct_ids
        for left_index, left_id in enumerate(ranked):
            left = state.aggregate.trial_evaluations[left_id]
            if left.decision is not TrialDecision.INELIGIBLE:
                continue
            verified_fail_above_nonfail += sum(
                state.aggregate.trial_evaluations[right_id].decision
                in {
                    TrialDecision.PRE_SCREEN_PASS,
                    TrialDecision.POTENTIAL_MATCH,
                    TrialDecision.REVIEW_REQUIRED,
                }
                for right_id in ranked[left_index + 1 :]
            )
        for nct_id in ranked[:3]:
            compiled = corpus.compiled_trials[nct_id]
            critical = [
                criterion for criterion in compiled.criteria if criterion.criticality == "CRITICAL"
            ]
            material_criteria += len(critical)
            material_opaque += sum(criterion.opaque for criterion in critical)
            for proof in state.proofs_by_trial[nct_id]:
                if proof.final_verdict not in hard_verdicts:
                    continue
                displayed_hard_verdicts += 1
                review_check = next(
                    check for check in proof.verifier_checks if check.check_id == "PV-004"
                )
                displayed_hard_approved += review_check.passed

    coverages = [trial.source_character_coverage for trial in corpus.compiled_trials.values()]
    return {
        "scope": "DATASET_A_RELEASE_INVARIANTS",
        "acceptance_eligible": True,
        "safety_metrics": {
            "grade_h_hard_decision_occurrences": grade_h_hard_occurrences,
            "unsupported_hard_decision_rate": (
                unsupported_hard_occurrences / hard_proof_count if hard_proof_count else 0.0
            ),
            "proof_replay_success_rate": replay_successes / proof_count if proof_count else 0.0,
            "explanation_verdict_consistency": (
                explanation_successes / proof_count if proof_count else 0.0
            ),
            "opaque_hard_verdict_occurrences": opaque_hard_occurrences,
            "verified_fail_above_nonfail_occurrences": verified_fail_above_nonfail,
            "missing_value_default_decision_occurrences": missing_default_occurrences,
        },
        "protocol_metrics": {
            "protocol_min_character_coverage": min(coverages) if coverages else 0.0,
            "protocol_mean_character_coverage": (
                sum(coverages) / len(coverages) if coverages else 0.0
            ),
            "boundary_test_pass_rate": (
                sum(trial.boundary_tests_passed for trial in corpus.compiled_trials.values())
                / len(corpus.compiled_trials)
                if corpus.compiled_trials
                else 0.0
            ),
            "top3_material_opaque_rate": (
                material_opaque / material_criteria if material_criteria else 0.0
            ),
            "displayed_hard_verdict_review_approval_rate": (
                displayed_hard_approved / displayed_hard_verdicts
                if displayed_hard_verdicts
                else 1.0
            ),
        },
    }
