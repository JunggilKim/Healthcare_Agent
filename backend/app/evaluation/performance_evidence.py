from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel


class ReleasePerformanceEvidence(StrictModel):
    schema_version: Literal["trial-opt-performance-v1"] = "trial-opt-performance-v1"
    status: Literal["COMPLETE"]
    source_git_sha: str
    measured_at: datetime
    production_url: str
    snapshot_initial_analysis_seconds: list[float] = Field(min_length=20)
    snapshot_answer_reevaluation_seconds: list[float] = Field(min_length=20)
    warm_cache_live_seconds: list[float] = Field(min_length=20)
    cold_live_seconds: list[float] = Field(min_length=10)
    live_answer_reevaluation_seconds: list[float] = Field(min_length=1)
    golden_dependency_failure_fallback_seconds: list[float] = Field(min_length=1)
    container_startup_health_seconds: list[float] = Field(min_length=1)
    raw_patient_text_log_occurrences: int = Field(ge=0)
    structured_log_artifact_sha256: str
    live_run_ids: list[str] = Field(min_length=20)

    @model_validator(mode="after")
    def validate_evidence(self) -> ReleasePerformanceEvidence:
        if len(self.source_git_sha) != 40:
            raise ValueError("PERFORMANCE_GIT_SHA_INVALID")
        if not self.production_url.startswith("https://"):
            raise ValueError("PERFORMANCE_PRODUCTION_URL_INVALID")
        if len(self.structured_log_artifact_sha256) != 64:
            raise ValueError("PERFORMANCE_LOG_HASH_INVALID")
        if len(set(self.live_run_ids)) != len(self.live_run_ids):
            raise ValueError("PERFORMANCE_LIVE_RUN_ID_DUPLICATE")
        for field_name in (
            "snapshot_initial_analysis_seconds",
            "snapshot_answer_reevaluation_seconds",
            "warm_cache_live_seconds",
            "cold_live_seconds",
            "live_answer_reevaluation_seconds",
            "golden_dependency_failure_fallback_seconds",
            "container_startup_health_seconds",
        ):
            if any(value < 0 or not math.isfinite(value) for value in getattr(self, field_name)):
                raise ValueError(f"PERFORMANCE_DURATION_INVALID:{field_name}")
        return self


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    if not values or percentile <= 0 or percentile > 1:
        raise ValueError("PERFORMANCE_PERCENTILE_INPUT_INVALID")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def performance_acceptance_metrics(evidence: ReleasePerformanceEvidence) -> dict[str, Any]:
    return {
        "snapshot_initial_analysis_p95_seconds": percentile_nearest_rank(
            evidence.snapshot_initial_analysis_seconds, 0.95
        ),
        "snapshot_initial_analysis_run_count": len(evidence.snapshot_initial_analysis_seconds),
        "snapshot_answer_reevaluation_p95_seconds": percentile_nearest_rank(
            evidence.snapshot_answer_reevaluation_seconds, 0.95
        ),
        "snapshot_answer_reevaluation_run_count": len(
            evidence.snapshot_answer_reevaluation_seconds
        ),
        "warm_cache_live_p95_seconds": percentile_nearest_rank(
            evidence.warm_cache_live_seconds, 0.95
        ),
        "warm_cache_live_run_count": len(evidence.warm_cache_live_seconds),
        "cold_live_p95_seconds": percentile_nearest_rank(evidence.cold_live_seconds, 0.95),
        "cold_live_run_count": len(evidence.cold_live_seconds),
        "live_answer_reevaluation_p95_seconds": percentile_nearest_rank(
            evidence.live_answer_reevaluation_seconds, 0.95
        ),
        "golden_dependency_failure_fallback_max_seconds": max(
            evidence.golden_dependency_failure_fallback_seconds
        ),
        "container_startup_health_seconds": max(evidence.container_startup_health_seconds),
        "raw_patient_text_log_occurrences": evidence.raw_patient_text_log_occurrences,
    }
