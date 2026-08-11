from __future__ import annotations

import argparse
import sys
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.evaluation.performance_evidence import (  # noqa: E402
    ReleasePerformanceEvidence,
    performance_acceptance_metrics,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate commit-bound controlled local/live performance evidence"
    )
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    evidence = ReleasePerformanceEvidence.model_validate(orjson.loads(args.input.read_bytes()))
    metrics = performance_acceptance_metrics(evidence)
    passed = all(
        (
            metrics["snapshot_initial_analysis_p95_seconds"] < 3,
            metrics["snapshot_answer_reevaluation_p95_seconds"] < 1,
            metrics["warm_cache_live_p95_seconds"] < 30,
            metrics["cold_live_p95_seconds"] < 90,
            metrics["live_answer_reevaluation_p95_seconds"] < 5,
            metrics["golden_dependency_failure_fallback_max_seconds"] <= 12,
            metrics["container_startup_health_seconds"] <= 15,
            metrics["raw_patient_text_log_occurrences"] == 0,
        )
    )
    print(
        orjson.dumps(
            {
                "status": evidence.status,
                "source_git_sha": evidence.source_git_sha,
                "passed": passed,
                "metrics": metrics,
            }
        ).decode()
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
