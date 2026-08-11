from __future__ import annotations

from typing import Any

import pytest

from backend.app.application.vertical_slice import VerticalSliceFixture, load_vertical_slice
from backend.app.evaluation.corpus import ReleaseCorpus, build_release_corpus
from backend.app.evaluation.interactive import QuestionPolicy, run_question_policy
from backend.app.evaluation.models import MissingnessObservation, PatientWorld
from backend.app.evaluation.policy_evidence import DirectLLMChoiceStep
from backend.app.evaluation.worlds import generate_fixture_benchmark


def _case() -> tuple[VerticalSliceFixture, PatientWorld, MissingnessObservation, ReleaseCorpus]:
    fixture = load_vertical_slice()
    benchmark = generate_fixture_benchmark(fixture, 20260811)
    observation = next(
        item for item in benchmark.observations if item.rate == 0.4 and item.pattern == "REALISTIC"
    )
    world = next(item for item in benchmark.worlds if item.world_id == observation.world_id)
    corpus = build_release_corpus(
        [fixture.compiled_trial],
        [fixture.raw_trial],
        [fixture.review],
    )
    return fixture, world, observation, corpus


def _run(
    policy: QuestionPolicy,
    *,
    direct_llm_steps: list[DirectLLMChoiceStep] | None = None,
) -> dict[str, Any]:
    fixture, world, observation, corpus = _case()
    return run_question_policy(
        policy=policy,
        world=world,
        observation=observation,
        corpus=corpus,
        retrieval_scores={fixture.compiled_trial.nct_id: 1.0},
        exact_condition_matches={fixture.compiled_trial.nct_id: True},
        detailed_nct_ids=[fixture.compiled_trial.nct_id],
        seed=20260811,
        max_questions=5,
        direct_llm_steps=direct_llm_steps,
    )


def test_b6_uses_real_proofs_and_incremental_reevaluation() -> None:
    row = _run("B6")

    assert row["questions"] > 0
    assert row["accuracy_curve"][-1] == 1.0
    assert row["final_top3"] == row["target_top3"]
    assert row["recompiled_trial_ids"] == []
    assert len(row["resolved_critical_per_question"]) == row["questions"]


def test_random_baseline_is_repeatable_for_the_same_seed() -> None:
    assert _run("B2") == _run("B2")


def test_b5_must_choose_from_the_actual_candidate_list() -> None:
    with pytest.raises(ValueError, match="B5_RECORDED_CANDIDATE_LIST_MISMATCH"):
        _run(
            "B5",
            direct_llm_steps=[
                DirectLLMChoiceStep(
                    step_index=0,
                    candidate_slot_ids=["unsupported.slot"],
                    selected_slot_id="unsupported.slot",
                    prompt_sha256="a" * 64,
                    response_sha256="b" * 64,
                )
            ],
        )
