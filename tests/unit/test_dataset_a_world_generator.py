from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import CriterionVerdict
from backend.app.evaluation.models import PatientWorld
from backend.app.evaluation.world_generator import (
    generate_dataset_a_benchmark,
    generate_trial_worlds,
)
from backend.app.evaluation.worlds import _observations


def test_generic_generator_builds_all_target_world_types_from_verified_ast() -> None:
    fixture = load_vertical_slice()
    first, coverage = generate_trial_worlds(
        fixture.compiled_trial, evaluation_date=date(2026, 8, 11)
    )
    second, _ = generate_trial_worlds(fixture.compiled_trial, evaluation_date=date(2026, 8, 11))

    assert len(first) == 9
    assert coverage == {
        "FULL_PASS": 2,
        "SINGLE_FAIL": 2,
        "MULTI_FAIL": 1,
        "UNKNOWN": 2,
        "CONFLICT": 1,
        "BOUNDARY": 1,
    }
    assert canonical_json_bytes([item.model_dump(mode="json") for item in first]) == (
        canonical_json_bytes([item.model_dump(mode="json") for item in second])
    )
    assert all(
        world.fact_span_map.keys() == {fact.fact_id for fact in world.facts} for world in first
    )
    assert all(world.nct_id not in world.narrative for world in first)


def test_narrative_fact_spans_are_tamper_evident() -> None:
    fixture = load_vertical_slice()
    worlds, _ = generate_trial_worlds(fixture.compiled_trial, evaluation_date=date(2026, 8, 11))
    payload = worlds[0].model_dump(mode="json")
    payload["narrative"] = "tampered " + payload["narrative"]

    with pytest.raises(ValidationError, match="WORLD_FACT_SPAN_QUOTE_MISMATCH"):
        PatientWorld.model_validate(payload)


def test_release_generator_rejects_corpus_below_frozen_trial_range() -> None:
    fixture = load_vertical_slice()
    with pytest.raises(ValueError, match="DATASET_A_TRIAL_COUNT_MUST_BE_24_TO_36"):
        generate_dataset_a_benchmark(
            [fixture.compiled_trial],
            [fixture.raw_trial],
            seed=20260811,
            evaluation_date=date(2026, 8, 11),
        )


def test_worlds_persist_opaque_criteria_as_unknown_without_using_them_as_targets() -> None:
    fixture = load_vertical_slice()
    seed = fixture.compiled_trial.criteria[0]
    opaque = seed.model_copy(
        update={
            "criterion_id": f"{seed.nct_id}:INCLUSION:999:opaque",
            "ast": CriterionAst(
                root_node_id="opaque:0",
                nodes=[
                    AstNode(
                        node_id="opaque:0",
                        op=AstOperator.OPAQUE,
                        metadata={
                            "reason_code": "UNSUPPORTED_SOURCE_SEMANTICS",
                            "residual_source_sha256": seed.source_text_sha256,
                        },
                    )
                ],
            ),
            "required_slots": [],
            "protocol_verified": False,
            "opaque": True,
        }
    )
    trial = fixture.compiled_trial.model_copy(
        update={"criteria": [*fixture.compiled_trial.criteria, opaque]}
    )

    worlds, _ = generate_trial_worlds(trial, evaluation_date=date(2026, 8, 11))

    opaque_truth = [
        truth
        for world in worlds
        for truth in world.criterion_truth
        if truth.criterion_id == opaque.criterion_id
    ]
    assert len(opaque_truth) == len(worlds)
    assert {truth.verdict for truth in opaque_truth} == {CriterionVerdict.UNKNOWN}


def test_complete_missing_world_does_not_request_impossible_missingness_sample() -> None:
    fixture = load_vertical_slice()
    worlds, _ = generate_trial_worlds(fixture.compiled_trial, evaluation_date=date(2026, 8, 11))
    empty = worlds[0].model_copy(
        update={
            "facts": [],
            "template_narrative": "Synthetic structured record. .",
            "narrative": "Synthetic structured record. .",
            "fact_span_map": {},
        }
    )

    assert _observations([empty], 20260811) == []
