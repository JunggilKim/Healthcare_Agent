from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from backend.app.application.catalog import load_slot_catalog
from backend.app.application.vertical_slice import FIXTURE_DIR, load_vertical_slice
from backend.app.domain.evidence import EligibilityContext, PatientFact
from backend.app.engine.proof_verifier import build_verified_proof, replay_packet_matches
from backend.app.engine.question_optimizer import OptimizationState, select_next_action
from backend.app.engine.trial_aggregator import aggregate_trial


def _build_state() -> OptimizationState:
    fixture = load_vertical_slice()
    slots = load_slot_catalog().by_id()
    context = EligibilityContext(facts=list(fixture.facts), conflicts=list(fixture.conflicts))
    proofs = [
        build_verified_proof(
            session_id="00000000-0000-4000-8000-000000000004",
            patient_state_version=0,
            evaluation_date=date(2026, 8, 11),
            criterion=criterion,
            compiled_trial=fixture.compiled_trial,
            review=fixture.review,
            raw_trial=fixture.raw_trial,
            registry_data_version="2026-08-11T09:00:06",
            eligibility_context=context,
            source_texts=fixture.source_texts,
            slots=slots,
            evaluated_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        )
        for criterion in fixture.compiled_trial.criteria
    ]
    evaluation = aggregate_trial(
        session_id="00000000-0000-4000-8000-000000000004",
        patient_state_version=0,
        compiled_trial=fixture.compiled_trial,
        raw_trial=fixture.raw_trial,
        proofs=proofs,
        retrieval_score=1.0,
    )
    return OptimizationState(
        session_id="00000000-0000-4000-8000-000000000004",
        patient_state_version=0,
        evaluation_date=date(2026, 8, 11),
        facts=list(fixture.facts),
        conflicts=list(fixture.conflicts),
        source_texts=dict(fixture.source_texts),
        compiled_trial=fixture.compiled_trial,
        review=fixture.review,
        raw_trial=fixture.raw_trial,
        registry_data_version="2026-08-11T09:00:06",
        proofs=proofs,
        trial_evaluation=evaluation,
        slots=slots,
        enabled_acquisition_slots=fixture.enabled_acquisition_slots,
    )


def test_frozen_initial_state_has_age_pass_histology_unknown_and_firewall() -> None:
    state = _build_state()
    verdicts = {proof.criterion_id: proof.final_verdict.value for proof in state.proofs}
    assert verdicts["NCT05239624:INCLUSION:001:443174ab"] == "PASS"
    assert verdicts["NCT05239624:INCLUSION:002:5f52ab88"] == "UNKNOWN"
    assert all(
        not evidence_id.startswith("hyp_")
        for proof in state.proofs
        for evidence_id in proof.evidence_fact_ids
    )
    assert all(
        next(check for check in proof.verifier_checks if check.check_id == "PV-007").passed
        for proof in state.proofs
    )
    assert all(
        replay_packet_matches(
            proof,
            next(
                item
                for item in state.compiled_trial.criteria
                if item.criterion_id == proof.criterion_id
            ),
            EligibilityContext(facts=state.facts, conflicts=state.conflicts),
        )
        for proof in state.proofs
    )


def test_optimizer_selects_histology_then_keeps_invasion_unknown() -> None:
    state = _build_state()
    first = select_next_action(state)
    assert first.selected is not None
    assert first.selected.slot_id == "pathology.histology"

    answer_payload = load_vertical_slice().answers["pathology_histology"]["branch_a"]
    answer_fact = PatientFact.model_validate(answer_payload["fact"])
    answer_source_id = answer_fact.source_spans[0].source_id
    state.facts.append(answer_fact)
    state.source_texts[answer_source_id] = answer_payload["answer_text"]
    state.patient_state_version = 1
    state.question_count = 1
    state.asked_slot_ids.append("pathology.histology")

    context = EligibilityContext(facts=state.facts, conflicts=state.conflicts)
    state.proofs = [
        build_verified_proof(
            session_id=state.session_id,
            patient_state_version=1,
            evaluation_date=state.evaluation_date,
            criterion=criterion,
            compiled_trial=state.compiled_trial,
            review=state.review,
            raw_trial=state.raw_trial,
            registry_data_version=state.registry_data_version,
            eligibility_context=context,
            source_texts=state.source_texts,
            slots=state.slots,
            evaluated_at=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
        )
        for criterion in state.compiled_trial.criteria
    ]
    state.trial_evaluation = aggregate_trial(
        session_id=state.session_id,
        patient_state_version=1,
        compiled_trial=state.compiled_trial,
        raw_trial=state.raw_trial,
        proofs=state.proofs,
        retrieval_score=1.0,
    )
    verdicts = {proof.criterion_id: proof.final_verdict.value for proof in state.proofs}
    assert verdicts["NCT05239624:INCLUSION:002:5f52ab88"] == "PASS"
    assert verdicts["NCT05239624:INCLUSION:003:a7db6608"] == "UNKNOWN"
    second = select_next_action(state)
    assert second.selected is not None
    assert second.selected.slot_id == "pathology.muscle_invasion"


def test_branch_b_marks_histology_unavailable_and_does_not_repeat_it() -> None:
    state = _build_state()
    state.unavailable_slot_ids.add("pathology.histology")
    state.asked_slot_ids.append("pathology.histology")
    state.question_count = 1
    selection = select_next_action(state)
    assert selection.selected is not None
    assert selection.selected.slot_id == "pathology.muscle_invasion"


def test_fixture_files_are_all_present() -> None:
    required = {
        "NCT05239624.compact.json",
        "NCT05239624.criteria.json",
        "NCT05239624.review.json",
        "optimizer_scope.yaml",
        "manifest.yaml",
    }
    assert required <= {path.name for path in Path(FIXTURE_DIR).iterdir()}
