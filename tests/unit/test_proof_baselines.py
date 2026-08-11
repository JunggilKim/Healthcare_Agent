from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.evaluation.annotations import (
    AdjudicatedAnnotation,
    AnnotationVerdict,
    build_annotation_assignments,
)
from backend.app.evaluation.proof_baselines import (
    ProofBaselineEvidence,
    ProofBaselinePrediction,
    evaluate_proof_baselines,
    validate_proof_baseline_evidence,
)
from backend.app.evaluation.worlds import generate_fixture_benchmark


def test_paid_proof_baselines_are_hash_bound_and_evaluated() -> None:
    fixture = load_vertical_slice()
    benchmark = generate_fixture_benchmark(fixture, 20260811)
    assignment = build_annotation_assignments(
        benchmark,
        [fixture.compiled_trial],
        seed=20260811,
        sample_size=1,
        dual_review_size=0,
    )[0]
    truth = next(
        item
        for world in benchmark.worlds
        if world.world_id == assignment.world_id
        for item in world.criterion_truth
        if item.criterion_id == assignment.criterion_id
    )
    annotation_manifest_bytes = b"annotation-manifest"
    assignment_bytes = canonical_json_bytes(assignment.model_dump(mode="json")) + b"\n"
    evidence = ProofBaselineEvidence(
        status="BATCH_COMPLETED",
        annotation_manifest_sha256=hashlib.sha256(annotation_manifest_bytes).hexdigest(),
        assignment_jsonl_sha256=hashlib.sha256(assignment_bytes).hexdigest(),
        model_id="gemini-3.6-flash",
        prompt_version="proof-baseline-v1",
        batch_job_name="projects/test/locations/global/batchPredictionJobs/1",
        completed_at=datetime(2026, 8, 12, tzinfo=UTC),
        predictions=[
            ProofBaselinePrediction(
                record_id=assignment.record_id,
                assignment_hash=assignment.assignment_hash,
                p0_verdict=AnnotationVerdict(truth.verdict.value),
                p0_explanation="The explicit synthetic fact matches the criterion.",
                p1_verdict=AnnotationVerdict(truth.verdict.value),
                p1_evidence_fact_ids=truth.evidence_fact_ids,
                p1_explanation="The cited synthetic fact supports the verdict.",
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
            )
        ],
    )
    validate_proof_baseline_evidence(
        evidence,
        annotation_manifest_bytes=annotation_manifest_bytes,
        assignment_jsonl_bytes=assignment_bytes,
        assignments=[assignment],
    )
    gold = [
        AdjudicatedAnnotation(
            record_id=assignment.record_id,
            assignment_hash=assignment.assignment_hash,
            verdict=AnnotationVerdict(truth.verdict.value),
            evidence_fact_ids=truth.evidence_fact_ids,
            missing_slot_ids=truth.missing_slot_ids,
            safely_executable=True,
            explanation_supported=True,
            reviewer_aliases=["reviewer-a"],
            adjudicator_alias=None,
            disagreement=False,
            finalized_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
    ]

    baselines = evaluate_proof_baselines(
        evidence,
        assignments=[assignment],
        gold=gold,
        p2_p3_criterion_metrics={"macro_f1": 1.0},
        p3_unsupported_hard_decision_rate=0.0,
        p3_proof_replay_success_rate=1.0,
    )

    assert baselines["P0"]["status"] == "BATCH_COMPLETED"
    assert baselines["P1"]["status"] == "BATCH_COMPLETED"
    assert baselines["P2"]["status"] == "COMPLETED"
    assert baselines["P3"]["proof_replay_success_rate"] == 1.0
