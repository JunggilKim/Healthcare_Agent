from __future__ import annotations

from datetime import UTC, datetime

from backend.app.evaluation.performance_evidence import (
    ReleasePerformanceEvidence,
    performance_acceptance_metrics,
)


def test_performance_evidence_uses_conservative_nearest_rank_percentiles() -> None:
    evidence = ReleasePerformanceEvidence(
        status="COMPLETE",
        source_git_sha="a" * 40,
        measured_at=datetime(2026, 8, 12, tzinfo=UTC),
        production_url="https://trial-opt.example.test",
        snapshot_initial_analysis_seconds=[float(index) / 100 for index in range(1, 21)],
        snapshot_answer_reevaluation_seconds=[float(index) / 1000 for index in range(1, 21)],
        warm_cache_live_seconds=[float(index) for index in range(1, 21)],
        cold_live_seconds=[float(index) for index in range(1, 11)],
        live_answer_reevaluation_seconds=[1.5],
        golden_dependency_failure_fallback_seconds=[2.5, 3.0],
        container_startup_health_seconds=[4.0],
        raw_patient_text_log_occurrences=0,
        structured_log_artifact_sha256="b" * 64,
        live_run_ids=[f"run-{index}" for index in range(20)],
    )

    metrics = performance_acceptance_metrics(evidence)

    assert metrics["snapshot_initial_analysis_p95_seconds"] == 0.19
    assert metrics["warm_cache_live_p95_seconds"] == 19.0
    assert metrics["cold_live_p95_seconds"] == 10.0
    assert metrics["golden_dependency_failure_fallback_max_seconds"] == 3.0
