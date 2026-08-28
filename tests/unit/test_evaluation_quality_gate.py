from __future__ import annotations

from typing import Any

from backend.app.evaluation.quality_gate import evaluate_engineering_quality_gate


def _suites(sha: str = "abc123") -> dict[str, dict[str, Any]]:
    metadata = {"git_sha": sha}
    return {
        "retrieval": {
            "metadata": metadata,
            "metrics": {"baselines": {"full_three_source_rrf": {"recall_at_20": 1.0}}},
        },
        "criterion": {
            "metadata": metadata,
            "metrics": {
                "criterion_metrics": {"count": 63, "accuracy": 1.0},
                "hard_fail_recall": 1.0,
                "false_pre_screen_pass_rate": 0.0,
            },
        },
        "interactive": {
            "metadata": metadata,
            "metrics": {
                "policies": {
                    "B0": {"final_decision_accuracy": 0.6},
                    "B6": {"final_decision_accuracy": 1.0},
                }
            },
        },
        "ablation": {
            "metadata": metadata,
            "metrics": {
                "ablations": {
                    f"A{index}": {"final_decision_accuracy": 1.0} for index in range(1, 9)
                }
            },
        },
    }


def test_quality_gate_passes_current_commit_engineering_bounds() -> None:
    result = evaluate_engineering_quality_gate(_suites(), expected_git_sha="abc123")

    assert result["passed"] is True
    assert result["clinical_validation"] is False


def test_quality_gate_rejects_stale_or_regressed_results() -> None:
    suites = _suites(sha="old")
    suites["criterion"]["metrics"]["false_pre_screen_pass_rate"] = 0.1

    result = evaluate_engineering_quality_gate(suites, expected_git_sha="current")

    assert result["passed"] is False
    failed_names = {item["name"] for item in result["checks"] if not item["passed"]}
    assert "criterion.git_sha" in failed_names
    assert "criterion.false_pre_screen_pass_rate" in failed_names
