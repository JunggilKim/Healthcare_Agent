from __future__ import annotations

from datetime import UTC, date, datetime

import backend.app.engine.multi_trial_optimizer as optimizer_module
from backend.app.agents.answer_interpreter import interpret_answer
from backend.app.agents.question_renderer import render_question
from backend.app.agents.report_renderer import (
    deterministic_criterion_explanation,
    validate_or_fallback_report,
)
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.interactive_loop import InteractiveTrialOptLoop
from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.enums import AcquisitionAction, CriterionVerdict, TrialDecision
from backend.app.domain.evidence import EligibilityContext, PatientFact
from backend.app.domain.questions import OptimizerRuntimeConfig
from backend.app.domain.rendering import QuestionRenderProposal, TrialReportProposal
from backend.app.domain.sessions import SessionAggregate
from backend.app.domain.values import CategoricalValue, NumberValue, StringValue
from backend.app.engine.branch_builder import build_branches, deterministic_question_id
from backend.app.engine.incremental import build_reverse_slot_index, reevaluate_for_answered_slot
from backend.app.engine.multi_trial_optimizer import (
    FullOptimizationState,
    OptimizerScoringFlags,
    branch_discrimination,
    generate_slot_candidates,
    select_next_action,
)
from backend.app.engine.proof_verifier import (
    build_post_render_proof,
    build_verified_proof,
    replay_packet_matches,
)
from backend.app.engine.trial_aggregator import aggregate_trial

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
SESSION_ID = "00000000-0000-4000-8000-000000000044"


def _full_state() -> FullOptimizationState:
    fixture = load_vertical_slice()
    slots = load_slot_catalog().by_id()
    proofs = [
        build_verified_proof(
            session_id=SESSION_ID,
            patient_state_version=0,
            evaluation_date=date(2026, 8, 11),
            criterion=criterion,
            compiled_trial=fixture.compiled_trial,
            review=fixture.review,
            raw_trial=fixture.raw_trial,
            registry_data_version="2026-08-11T09:00:06",
            eligibility_context=EligibilityContext(
                facts=list(fixture.facts), conflicts=list(fixture.conflicts)
            ),
            source_texts=fixture.source_texts,
            slots=slots,
            evaluated_at=NOW,
        )
        for criterion in fixture.compiled_trial.criteria
    ]
    evaluation = aggregate_trial(
        session_id=SESSION_ID,
        patient_state_version=0,
        compiled_trial=fixture.compiled_trial,
        raw_trial=fixture.raw_trial,
        proofs=proofs,
        retrieval_score=1.0,
    )
    aggregate = SessionAggregate(
        session_id=SESSION_ID,
        mode="snapshot",
        evaluation_date=date(2026, 8, 11),
        patient_state_version=0,
        question_count=0,
        facts=list(fixture.facts),
        retrieval_hypotheses=list(fixture.hypotheses),
        conflicts=list(fixture.conflicts),
        compiled_trials={fixture.compiled_trial.nct_id: fixture.compiled_trial},
        trial_evaluations={evaluation.nct_id: evaluation},
        ranked_nct_ids=[evaluation.nct_id],
        asked_slot_ids=[],
        unavailable_slot_ids=[],
        current_question_id=None,
        config=OptimizerRuntimeConfig(
            top_k=5,
            max_questions=5,
            hard_max_questions=7,
            max_branches=6,
            stop_utility_threshold=0.10,
            stable_risk_reduction_threshold=0.05,
        ),
    )
    return FullOptimizationState(
        aggregate=aggregate,
        proofs_by_trial={evaluation.nct_id: proofs},
        raw_trials={evaluation.nct_id: fixture.raw_trial},
        reviews={evaluation.nct_id: fixture.review},
        registry_data_versions={evaluation.nct_id: "2026-08-11T09:00:06"},
        source_texts=dict(fixture.source_texts),
        slots=slots,
        evaluated_at=NOW,
    )


def test_full_optimizer_uses_real_utility_and_exposes_top_candidates() -> None:
    state = _full_state()
    selection = select_next_action(state)
    assert selection.selected is not None
    assert 1 <= len(selection.top_alternatives) <= 3
    assert all(item.utility_components is not None for item in selection.top_alternatives)
    assert state.recompiled_trial_ids == []


def test_candidate_branch_inputs_preserve_canonical_rank_order(monkeypatch) -> None:
    state = _full_state()
    base_trial = next(iter(state.aggregate.compiled_trials.values()))
    base_evaluation = next(iter(state.aggregate.trial_evaluations.values()))
    base_proofs = next(iter(state.proofs_by_trial.values()))
    ranked_ids = [f"NCT0000000{index}" for index in range(1, 6)]
    trials = {}
    evaluations = {}
    proofs_by_trial = {}
    histology_ids = []
    for nct_id in reversed(ranked_ids):
        criteria = []
        proofs = []
        paired = zip(base_trial.criteria, base_proofs, strict=True)
        for index, (criterion, proof) in enumerate(paired):
            criterion_id = f"{nct_id}:criterion:{index}"
            criteria.append(criterion.model_copy(update={"criterion_id": criterion_id}))
            proofs.append(proof.model_copy(update={"criterion_id": criterion_id, "nct_id": nct_id}))
            if "pathology.histology" in criterion.required_slots:
                histology_ids.append((nct_id, criterion_id))
        trials[nct_id] = base_trial.model_copy(update={"nct_id": nct_id, "criteria": criteria})
        evaluations[nct_id] = base_evaluation.model_copy(update={"nct_id": nct_id})
        proofs_by_trial[nct_id] = proofs

    state.aggregate = state.aggregate.model_copy(
        update={
            "compiled_trials": trials,
            "trial_evaluations": evaluations,
            "ranked_nct_ids": ranked_ids,
        }
    )
    state.proofs_by_trial = proofs_by_trial
    observed: list[str] = []
    real_build_branches = optimizer_module.build_branches

    def recording_build_branches(**kwargs):
        if kwargs["slot"].slot_id == "pathology.histology":
            observed.extend(item.criterion_id for item in kwargs["affected_criteria"])
        return real_build_branches(**kwargs)

    monkeypatch.setattr(optimizer_module, "build_branches", recording_build_branches)
    generate_slot_candidates(state)

    expected_by_rank = [
        criterion_id
        for nct_id in ranked_ids
        for owner, criterion_id in histology_ids
        if owner == nct_id
    ]
    assert observed == expected_by_rank


def test_optimizer_ablation_flags_change_shared_scoring_without_forking_policy() -> None:
    state = _full_state()
    full = select_next_action(state)
    no_minimum = select_next_action(
        state,
        scoring_flags=OptimizerScoringFlags(minimum_branch_utility=False),
    )
    assert full.top_alternatives
    assert no_minimum.top_alternatives
    full_by_slot = {item.slot_id: item for item in full.top_alternatives}
    no_minimum_by_slot = {item.slot_id: item for item in no_minimum.top_alternatives}
    shared_slots = set(full_by_slot) & set(no_minimum_by_slot)
    assert shared_slots
    for slot_id in shared_slots:
        full_components = full_by_slot[slot_id].utility_components
        ablated_components = no_minimum_by_slot[slot_id].utility_components
        assert full_components is not None and ablated_components is not None
        assert ablated_components.base_utility <= full_components.base_utility

    duplicated = generate_slot_candidates(state, slot_level_deduplication=False)
    deduplicated = generate_slot_candidates(state)
    assert len(duplicated) == sum(len(item.affected) for item in deduplicated)
    assert len({item.question_id for item in duplicated}) == len(duplicated)


def test_reverse_index_and_incremental_reevaluation_touch_only_affected_proof() -> None:
    state = _full_state()
    fixture = load_vertical_slice()
    index = build_reverse_slot_index(state.aggregate)
    histology_id = "NCT05239624:INCLUSION:002:5f52ab88"
    assert ("NCT05239624", histology_id) in index["pathology.histology"]
    answer = fixture.answers["pathology_histology"]["branch_a"]
    answer_fact = PatientFact.model_validate(answer["fact"])
    source_texts = dict(state.source_texts)
    source_texts[answer_fact.source_spans[0].source_id] = answer["answer_text"]
    before = {proof.criterion_id: proof for proof in state.proofs_by_trial["NCT05239624"]}
    result = reevaluate_for_answered_slot(
        aggregate=state.aggregate,
        answered_slot_id="pathology.histology",
        updated_facts=[*state.aggregate.facts, answer_fact],
        updated_conflicts=state.aggregate.conflicts,
        answer_fact_ids=[answer_fact.fact_id],
        proofs_by_trial=state.proofs_by_trial,
        raw_trials=state.raw_trials,
        reviews=state.reviews,
        registry_data_versions=state.registry_data_versions,
        source_texts=source_texts,
        slots=state.slots,
        evaluated_at=NOW,
    )
    after = {proof.criterion_id: proof for proof in result.proofs_by_trial["NCT05239624"]}
    assert result.changed_criterion_ids == [histology_id]
    assert result.recompiled_trial_ids == []
    assert after[histology_id].patient_state_version == 1
    assert after[histology_id].final_verdict.value == "PASS"
    assert all(
        after[criterion_id] is packet
        for criterion_id, packet in before.items()
        if criterion_id != histology_id
    )


def test_branch_builder_covers_boolean_numeric_and_uniform_weights() -> None:
    fixture = load_vertical_slice()
    slots = load_slot_catalog().by_id()
    boolean = build_branches(
        question_id=deterministic_question_id(SESSION_ID, 0, "pathology.muscle_invasion"),
        slot=slots["pathology.muscle_invasion"],
        affected_criteria=[fixture.compiled_trial.criteria[2]],
        evaluation_date=date(2026, 8, 11),
    )
    assert [branch.label for branch in boolean] == [
        "true",
        "false",
        "unknown_or_declined",
    ]
    numeric = build_branches(
        question_id=deterministic_question_id(SESSION_ID, 0, "demographics.age"),
        slot=slots["demographics.age"],
        affected_criteria=[fixture.compiled_trial.criteria[0]],
        evaluation_date=date(2026, 8, 11),
    )
    values = [branch.synthetic_value for branch in numeric if branch.synthetic_value]
    assert any(isinstance(value, NumberValue) and value.value == 18 for value in values)
    assert abs(sum(branch.weight for branch in numeric) - 1.0) <= 1e-9


def test_branch_builder_normalizes_canonical_string_ast_values_for_categorical_slot() -> None:
    fixture = load_vertical_slice()
    criterion = fixture.compiled_trial.criteria[0].model_copy(
        update={
            "required_slots": ["pregnancy.status"],
            "ast": CriterionAst(
                root_node_id="n0",
                nodes=[
                    AstNode(
                        node_id="n0",
                        op=AstOperator.EQ,
                        slot_id="pregnancy.status",
                        value=StringValue(kind="string", value="pregnant"),
                    )
                ],
            ),
        }
    )
    branches = build_branches(
        question_id=deterministic_question_id(SESSION_ID, 0, "pregnancy.status"),
        slot=load_slot_catalog().by_id()["pregnancy.status"],
        affected_criteria=[criterion],
        evaluation_date=date(2026, 8, 11),
    )

    values = [item.synthetic_value for item in branches if item.synthetic_value is not None]
    assert values
    assert all(isinstance(item, CategoricalValue) for item in values)


def test_branch_discrimination_exactly_distinguishes_decisions() -> None:
    outcomes = [
        {"NCT1": (1, TrialDecision.POTENTIAL_MATCH)},
        {"NCT1": (1, TrialDecision.INELIGIBLE)},
    ]
    assert 0.49 < branch_discrimination(outcomes, [0.5, 0.5]) < 0.51


def test_question_renderer_rejects_changed_action() -> None:
    selection = select_next_action(_full_state())
    assert selection.selected is not None
    candidate = selection.selected
    rendered = render_question(
        candidate=candidate,
        slot=load_slot_catalog().by_id()[candidate.slot_id],
        deterministic_rationale=selection.deterministic_rationale,
        proposal=QuestionRenderProposal(
            question_id=candidate.question_id,
            slot_id=candidate.slot_id,
            action=AcquisitionAction.CLINICIAN_REVIEW,
            answer_type=candidate.answer_type,
            question_ko="다른 질문",
            reason_ko="다른 이유",
        ),
    )
    assert rendered.source == "DETERMINISTIC_TEMPLATE"
    assert rendered.rejection_code == "QUESTION_IDENTIFIERS_CHANGED"


def test_answer_interpreter_materializes_only_selected_histology_slot() -> None:
    state = _full_state()
    candidate = next(
        item for item in generate_slot_candidates(state) if item.slot_id == "pathology.histology"
    )
    interpreted = interpret_answer(
        candidate=candidate,
        answer_text="Existing pathology report confirms high-grade urothelial carcinoma.",
        source_id="answer:test",
        slot_catalog=load_slot_catalog(),
        asserted_at=NOW,
    )
    assert interpreted.materialized is not None
    facts = interpreted.materialized.state.confirmed_facts
    assert [fact.slot_id for fact in facts] == ["pathology.histology"]
    assert all(fact.slot_id != "pathology.muscle_invasion" for fact in facts)


def test_pv15_creates_r1_without_changing_decision_packet() -> None:
    state = _full_state()
    packet = state.proofs_by_trial["NCT05239624"][0]
    explanation = deterministic_criterion_explanation(packet)
    rendered = build_post_render_proof(packet, explanation)
    assert packet.proof_revision == 0
    assert packet.verification_phase == "DECISION"
    assert rendered.proof_revision == 1
    assert rendered.supersedes_proof_id == packet.proof_id
    assert rendered.final_verdict is packet.final_verdict
    assert rendered.verifier_checks[-1].check_id == "PV-015"
    assert rendered.verifier_checks[-1].passed


def test_report_renderer_rejects_changed_status() -> None:
    state = _full_state()
    evaluation = state.aggregate.trial_evaluations["NCT05239624"]
    report = validate_or_fallback_report(
        evaluation=evaluation,
        decision_proofs=state.proofs_by_trial["NCT05239624"],
        proposal=TrialReportProposal(
            trial_id="NCT05239624",
            status=TrialDecision.PRE_SCREEN_PASS,
            summary_ko="확정되었습니다.",
            criterion_refs=[],
            evidence_refs=[],
        ),
    )
    assert report.source == "DETERMINISTIC_TEMPLATE"
    assert report.report.status is evaluation.decision
    assert report.rejection_code == "REPORT_STATUS_OR_TRIAL_MISMATCH"


def test_proof_replay_detects_a_tampered_verdict() -> None:
    state = _full_state()
    packet = state.proofs_by_trial["NCT05239624"][0]
    tampered = packet.model_copy(update={"provisional_verdict": CriterionVerdict.FAIL})
    criterion = next(
        item
        for item in state.aggregate.compiled_trials["NCT05239624"].criteria
        if item.criterion_id == packet.criterion_id
    )
    assert not replay_packet_matches(
        tampered,
        criterion,
        EligibilityContext(
            facts=state.aggregate.facts,
            conflicts=state.aggregate.conflicts,
        ),
    )


def test_interactive_loop_never_recompiles_and_enforces_five_question_budget() -> None:
    state = _full_state()
    state.aggregate = state.aggregate.model_copy(
        update={
            "config": state.aggregate.config.model_copy(
                update={
                    "stop_utility_threshold": -1.0,
                    "stable_risk_reduction_threshold": -1.0,
                }
            )
        }
    )
    loop = InteractiveTrialOptLoop(state, load_slot_catalog())
    selection = loop.prepare_next_question()
    answered = 0
    while selection.selected is not None and answered < 5:
        turn = loop.submit_answer(
            candidate=selection.selected,
            answer_text="unknown",
            source_id=f"answer:{answered}",
            asserted_at=NOW,
        )
        assert turn.recompiled_trial_ids == []
        selection = turn.next_selection
        answered += 1
    assert answered == 5
    assert state.aggregate.question_count == 5
    assert selection.selected is None
    assert selection.stop_reason == "MAX_QUESTION_BUDGET"
