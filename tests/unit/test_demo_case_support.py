from __future__ import annotations

from datetime import UTC, datetime

from backend.app.application.demo_cases import (
    demo_retrieval_concept,
    demo_support_level,
    load_demo_cases,
)
from backend.app.application.live_session_service import (
    _retrieval_only_seed_state,
    _reviewed_seed_protocols,
    _same_protocol_compilation_input,
)
from backend.app.retrieval.query_builder import build_deterministic_query


def test_all_ten_demo_cases_have_an_explicit_truthful_support_contract() -> None:
    cases = load_demo_cases()

    assert [item["num"] for item in cases] == [f"S{index:03d}" for index in range(1, 11)]
    assert {item["num"] for item in cases if item["support_level"] == "full_evaluation"} == {
        "S001",
        "S004",
        "S008",
    }
    assert {item["num"] for item in cases if item["support_level"] == "retrieval_only"} == {
        "S002",
        "S003",
        "S005",
        "S006",
        "S007",
        "S009",
        "S010",
    }
    assert all(demo_retrieval_concept(str(item["num"])) for item in cases)


def test_retrieval_only_seed_hypothesis_is_grade_h_and_never_eligibility_evidence() -> None:
    case = next(item for item in load_demo_cases() if item["num"] == "S006")
    state = _retrieval_only_seed_state(
        case_id="S006",
        patient_text=str(case["title"]),
        source_id="seed:S006",
        asserted_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    assert demo_support_level("S006") == "retrieval_only"
    assert state.retrieval_hypotheses[0].normalized_concept == "mucormycosis"
    assert state.retrieval_hypotheses[0].grade == "H"
    assert state.retrieval_hypotheses[0].admissible_for_eligibility is False
    presentation = next(
        fact for fact in state.confirmed_facts if fact.slot_id == "custom.seed_presentation"
    )
    assert presentation.admissible_for_hard_decision is False
    query = build_deterministic_query(state.confirmed_facts, state.retrieval_hypotheses)
    assert query.condition_queries[0].text == "mucormycosis"
    assert query.must_not_use_as_eligibility_evidence is True


def test_reviewed_protocol_reuse_requires_the_complete_compiler_input_to_match() -> None:
    reviewed_raw, _compiled, review = next(iter(_reviewed_seed_protocols("S001").values()))

    assert review.approved is True
    assert _same_protocol_compilation_input(reviewed_raw, reviewed_raw)
    changed_source = reviewed_raw.model_copy(
        update={"eligibility_criteria": f"{reviewed_raw.eligibility_criteria}\nChanged"}
    )
    changed_status = reviewed_raw.model_copy(update={"overall_status": "WITHDRAWN"})
    assert not _same_protocol_compilation_input(changed_source, reviewed_raw)
    assert not _same_protocol_compilation_input(changed_status, reviewed_raw)
