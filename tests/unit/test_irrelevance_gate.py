from __future__ import annotations

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.engine.trial_aggregator import is_trial_irrelevant
from backend.app.evaluation.execution import eligibility_context_from_world
from backend.app.evaluation.worlds import generate_fixture_benchmark


def test_irrelevance_gate_requires_all_three_conditions() -> None:
    fixture = load_vertical_slice()
    world = generate_fixture_benchmark(fixture, 20260811).worlds[0]
    context = eligibility_context_from_world(
        world.facts,
        world.conflict_slots,
        evaluation_date=world.evaluation_date,
        language=world.narrative_language,
    )

    assert is_trial_irrelevant(
        retrieval_score=0.14,
        exact_condition_match=False,
        compiled_trial=fixture.compiled_trial,
        facts=[],
    )
    assert not is_trial_irrelevant(
        retrieval_score=0.15,
        exact_condition_match=False,
        compiled_trial=fixture.compiled_trial,
        facts=[],
    )
    assert not is_trial_irrelevant(
        retrieval_score=0.14,
        exact_condition_match=True,
        compiled_trial=fixture.compiled_trial,
        facts=[],
    )
    assert not is_trial_irrelevant(
        retrieval_score=0.14,
        exact_condition_match=False,
        compiled_trial=fixture.compiled_trial,
        facts=context.facts,
    )
