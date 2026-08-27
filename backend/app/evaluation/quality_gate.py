from __future__ import annotations

from typing import Any


def _check(name: str, actual: object, expected: str, passed: bool) -> dict[str, object]:
    return {"name": name, "actual": actual, "expected": expected, "passed": passed}


def evaluate_engineering_quality_gate(
    suites: dict[str, dict[str, Any]], *, expected_git_sha: str
) -> dict[str, Any]:
    """Enforce deterministic regression bounds without claiming clinical validation."""

    required = {"retrieval", "criterion", "interactive", "ablation"}
    missing = sorted(required - suites.keys())
    if missing:
        return {
            "passed": False,
            "claim_scope": "deterministic engineering regression only",
            "clinical_validation": False,
            "checks": [
                _check("required_suites", missing, "no missing suites", False),
            ],
        }

    checks: list[dict[str, object]] = []
    for name in sorted(required):
        actual_sha = suites[name].get("metadata", {}).get("git_sha")
        checks.append(
            _check(
                f"{name}.git_sha",
                actual_sha,
                expected_git_sha,
                actual_sha == expected_git_sha,
            )
        )

    retrieval = suites["retrieval"]["metrics"]
    full_rrf = retrieval["baselines"]["full_three_source_rrf"]
    checks.append(
        _check(
            "retrieval.full_rrf_recall_at_20",
            full_rrf["recall_at_20"],
            ">= 1.0 on recorded fixture",
            float(full_rrf["recall_at_20"]) >= 1.0,
        )
    )

    criterion = suites["criterion"]["metrics"]
    criterion_metrics = criterion["criterion_metrics"]
    checks.extend(
        [
            _check(
                "criterion.fixture_count",
                criterion_metrics["count"],
                ">= 63",
                int(criterion_metrics["count"]) >= 63,
            ),
            _check(
                "criterion.accuracy",
                criterion_metrics["accuracy"],
                "1.0 self-consistency",
                float(criterion_metrics["accuracy"]) == 1.0,
            ),
            _check(
                "criterion.hard_fail_recall",
                criterion["hard_fail_recall"],
                "1.0",
                float(criterion["hard_fail_recall"]) == 1.0,
            ),
            _check(
                "criterion.false_pre_screen_pass_rate",
                criterion["false_pre_screen_pass_rate"],
                "0.0",
                float(criterion["false_pre_screen_pass_rate"]) == 0.0,
            ),
        ]
    )

    policies = suites["interactive"]["metrics"]["policies"]
    b0_accuracy = float(policies["B0"]["final_decision_accuracy"])
    b6_accuracy = float(policies["B6"]["final_decision_accuracy"])
    checks.extend(
        [
            _check(
                "interactive.b6_final_decision_accuracy",
                b6_accuracy,
                "1.0 on deterministic fixture",
                b6_accuracy == 1.0,
            ),
            _check(
                "interactive.b6_not_worse_than_b0",
                b6_accuracy - b0_accuracy,
                ">= 0.0",
                b6_accuracy >= b0_accuracy,
            ),
        ]
    )

    ablations = suites["ablation"]["metrics"]["ablations"]
    ablation_accuracies = {
        name: float(result["final_decision_accuracy"]) for name, result in sorted(ablations.items())
    }
    checks.append(
        _check(
            "ablation.final_decision_accuracy",
            ablation_accuracies,
            "all >= 1.0 on deterministic fixture",
            len(ablation_accuracies) == 8
            and all(value >= 1.0 for value in ablation_accuracies.values()),
        )
    )

    return {
        "passed": all(bool(item["passed"]) for item in checks),
        "claim_scope": "deterministic engineering regression only",
        "clinical_validation": False,
        "checks": checks,
    }
