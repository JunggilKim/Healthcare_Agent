from __future__ import annotations

from datetime import UTC, datetime

import orjson
import pytest
from pydantic import ValidationError

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.evaluation.annotations import (
    AnnotationAssignment,
    AnnotationReview,
    AnnotationVerdict,
    ReviewRole,
    adjudicate_annotations,
    build_annotation_assignments,
)
from backend.app.evaluation.execution import evaluate_adjudicated_subset
from backend.app.evaluation.models import BenchmarkArtifact


def _benchmark() -> BenchmarkArtifact:
    return BenchmarkArtifact.model_validate(
        orjson.loads(open("tests/fixtures/evaluation/benchmark.json", "rb").read())
    )


def _review(
    assignment: AnnotationAssignment,
    *,
    reviewer: str,
    role: ReviewRole,
    verdict: AnnotationVerdict = AnnotationVerdict.PASS,
    reason: str | None = None,
) -> AnnotationReview:
    return AnnotationReview(
        record_id=assignment.record_id,
        assignment_hash=assignment.assignment_hash,
        reviewer_alias=reviewer,
        role=role,
        revision=1,
        submitted_at=datetime(2026, 8, 12, tzinfo=UTC),
        blinded_to_system_output=True,
        verdict=verdict,
        evidence_fact_ids=[assignment.facts[0].fact_id],
        missing_slot_ids=[],
        safely_executable=True,
        explanation_supported=True,
        rationale="The pinned source and structured fact support this review label.",
        disagreement_reason=reason,
    )


def test_assignment_generation_is_deterministic_blinded_and_hash_bound() -> None:
    fixture = load_vertical_slice()
    first = build_annotation_assignments(
        _benchmark(), [fixture.compiled_trial], seed=20260811, sample_size=6, dual_review_size=2
    )
    second = build_annotation_assignments(
        _benchmark(), [fixture.compiled_trial], seed=20260811, sample_size=6, dual_review_size=2
    )

    assert [item.assignment_hash for item in first] == [item.assignment_hash for item in second]
    assert sum(item.dual_review_required for item in first) == 2
    serialized = orjson.dumps([item.model_dump(mode="json") for item in first])
    assert b"criterion_truth" not in serialized
    assert b'"verdict"' not in serialized

    tampered = first[0].model_dump(mode="json")
    tampered["narrative"] = "tampered"
    with pytest.raises(ValidationError, match="ASSIGNMENT_HASH_MISMATCH"):
        AnnotationAssignment.model_validate(tampered)


def test_release_annotation_selection_keeps_complete_world_bundles() -> None:
    fixture = load_vertical_slice()
    assignments = build_annotation_assignments(
        _benchmark(),
        [fixture.compiled_trial],
        seed=20260811,
        sample_size=8,
        dual_review_size=2,
        complete_world_bundles=True,
    )
    expected = {
        criterion.criterion_id
        for criterion in fixture.compiled_trial.criteria
        if not criterion.opaque
    }
    by_world: dict[str, set[str]] = {}
    for assignment in assignments:
        by_world.setdefault(assignment.world_id, set()).add(assignment.criterion_id)

    assert len(assignments) >= 8
    assert all(criterion_ids == expected for criterion_ids in by_world.values())


def test_dual_review_disagreement_requires_independent_adjudication() -> None:
    fixture = load_vertical_slice()
    assignments = build_annotation_assignments(
        _benchmark(), [fixture.compiled_trial], seed=20260811, sample_size=4, dual_review_size=2
    )
    reviews: list[AnnotationReview] = []
    dual = [item for item in assignments if item.dual_review_required]
    disagreement = dual[0]
    for assignment in assignments:
        reviews.append(_review(assignment, reviewer="reviewer-primary", role=ReviewRole.PRIMARY))
        if assignment.dual_review_required:
            reviews.append(
                _review(
                    assignment,
                    reviewer="reviewer-secondary",
                    role=ReviewRole.SECONDARY,
                    verdict=(
                        AnnotationVerdict.FAIL
                        if assignment.record_id == disagreement.record_id
                        else AnnotationVerdict.PASS
                    ),
                )
            )

    gold, incomplete = adjudicate_annotations(assignments, reviews)
    assert len(gold) == 3
    assert incomplete["incomplete"] == [f"{disagreement.record_id}:ADJUDICATION_REQUIRED"]

    reviews.append(
        _review(
            disagreement,
            reviewer="reviewer-adjudicator",
            role=ReviewRole.ADJUDICATOR,
            reason="The primary label follows the exact numeric boundary.",
        )
    )
    gold, summary = adjudicate_annotations(assignments, reviews)
    assert len(gold) == 4
    assert summary["completed_dual_reviews"] == 2
    assert summary["adjudicated_disagreements"] == 1
    assert summary["incomplete"] == []


def test_review_hash_and_reviewer_independence_are_enforced() -> None:
    fixture = load_vertical_slice()
    assignments = build_annotation_assignments(
        _benchmark(), [fixture.compiled_trial], seed=20260811, sample_size=1, dual_review_size=1
    )
    assignment = assignments[0]
    primary = _review(assignment, reviewer="same", role=ReviewRole.PRIMARY)
    secondary = _review(assignment, reviewer="same", role=ReviewRole.SECONDARY)
    with pytest.raises(ValueError, match="ANNOTATION_REVIEWERS_NOT_INDEPENDENT"):
        adjudicate_annotations(assignments, [primary, secondary])

    bad = secondary.model_copy(update={"reviewer_alias": "other", "assignment_hash": "0" * 64})
    with pytest.raises(ValueError, match="ANNOTATION_REVIEW_HASH_MISMATCH"):
        adjudicate_annotations(assignments, [primary, bad])


def test_adjudicated_subset_runs_actual_evaluator() -> None:
    fixture = load_vertical_slice()
    benchmark = _benchmark()
    assignments = build_annotation_assignments(
        benchmark, [fixture.compiled_trial], seed=20260811, sample_size=8, dual_review_size=2
    )
    truth_by_key = {
        (world.world_id, truth.criterion_id): truth
        for world in benchmark.worlds
        for truth in world.criterion_truth
    }
    reviews: list[AnnotationReview] = []
    for assignment in assignments:
        truth = truth_by_key[(assignment.world_id, assignment.criterion_id)]
        for role, reviewer in [(ReviewRole.PRIMARY, "primary")]:
            reviews.append(
                AnnotationReview(
                    record_id=assignment.record_id,
                    assignment_hash=assignment.assignment_hash,
                    reviewer_alias=reviewer,
                    role=role,
                    revision=1,
                    submitted_at=datetime(2026, 8, 12, tzinfo=UTC),
                    blinded_to_system_output=True,
                    verdict=AnnotationVerdict(truth.verdict.value),
                    evidence_fact_ids=truth.evidence_fact_ids,
                    missing_slot_ids=truth.missing_slot_ids,
                    safely_executable=True,
                    explanation_supported=True,
                    rationale="Independent fixture assertion for the evaluator contract test.",
                )
            )
        if assignment.dual_review_required:
            primary = reviews[-1]
            reviews.append(
                primary.model_copy(
                    update={"role": ReviewRole.SECONDARY, "reviewer_alias": "secondary"}
                )
            )
    gold, summary = adjudicate_annotations(assignments, reviews)
    assert summary["incomplete"] == []

    result = evaluate_adjudicated_subset(assignments, gold, [fixture.compiled_trial])

    assert result["criterion_metrics"]["accuracy"] == 1.0
    assert result["evidence_precision"] == 1.0
    assert result["acceptance_eligible"] is False
