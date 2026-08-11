from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib
import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "trial-opt-20260811"
import matplotlib.pyplot as plt  # noqa: E402

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402

LATEST_ROOT = Path("artifacts/eval/latest")
FRONTEND_SUMMARY = Path("frontend/public/eval/summary.json")


def _load_suite(name: str) -> dict[str, Any]:
    path = LATEST_ROOT / "suites" / f"{name}.json"
    if not path.is_file():
        raise RuntimeError(f"MISSING_EVAL_SUITE:{name}")
    return orjson.loads(path.read_bytes())


def _policy_curve(interactive: dict[str, Any], policy: str) -> list[dict[str, Any]]:
    return interactive["metrics"]["policies"][policy]["accuracy_by_question"]


def _write_charts(interactive: dict[str, Any]) -> list[str]:
    chart_root = LATEST_ROOT / "charts"
    chart_root.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.2))
    for policy, color in [("B0", "#64748b"), ("B3", "#f59e0b"), ("B6", "#0891b2")]:
        curve = _policy_curve(interactive, policy)
        plt.plot(
            [item["questions"] for item in curve],
            [item["accuracy"] for item in curve],
            marker="o",
            label=policy,
            color=color,
        )
    plt.ylim(0, 1.05)
    plt.xticks(range(6))
    plt.xlabel("Questions")
    plt.ylabel("Decision accuracy")
    plt.title("S004 fixture smoke: accuracy vs questions")
    plt.grid(alpha=0.2)
    plt.legend()
    plt.tight_layout()
    paths = []
    for extension in ("png", "svg"):
        path = chart_root / f"accuracy-vs-questions.{extension}"
        metadata = {"Date": None, "Creator": "TRIAL-OPT"} if extension == "svg" else None
        plt.savefig(path, dpi=160, metadata=metadata)
        paths.append(str(path))
    plt.close()
    return paths


def _write_predictions(suites: dict[str, dict[str, Any]]) -> int:
    rows = [
        row for document in suites.values() for row in document["metrics"].get("predictions", [])
    ]
    columns = sorted({key for row in rows for key in row})
    path = LATEST_ROOT / "predictions.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, list | dict)
                    else value
                    for key, value in row.items()
                }
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render committed evaluation JSON into reports")
    parser.add_argument("--latest", action="store_true")
    parser.parse_args()
    suites = {
        name: _load_suite(name) for name in ("retrieval", "criterion", "interactive", "ablation")
    }
    interactive = suites["interactive"]
    criterion = suites["criterion"]
    retrieval = suites["retrieval"]
    ablation = suites["ablation"]
    charts = _write_charts(interactive)
    prediction_count = _write_predictions(suites)
    policy_metrics = interactive["metrics"]["policies"]
    summary = {
        "schema_version": "trial-opt-eval-summary-v1",
        "claim_scope": "project-created S004 structured fixture engineering smoke",
        "acceptance_eligible": False,
        "clinical_validation": False,
        "blocking_reasons": [
            "Dataset A reviewed corpus and annotations are incomplete.",
            "The fixture contains one trial, so stable top-3 claims are not estimable.",
            "Paid LLM baselines B5, P0, and P1 were not run.",
        ],
        "run_ids": {name: document["metadata"]["run_id"] for name, document in suites.items()},
        "metrics": {
            "criterion_macro_f1_self_consistency": criterion["metrics"]["criterion_metrics"][
                "macro_f1"
            ],
            "unsupported_hard_decision_rate_fixture": criterion["metrics"]["proof_baselines"]["P3"][
                "unsupported_hard_decision_rate"
            ],
            "false_pre_screen_pass_rate_fixture": criterion["metrics"][
                "false_pre_screen_pass_rate"
            ],
            "retrieval_recall_at_20_proxy": retrieval["metrics"]["baselines"][
                "full_three_source_rrf"
            ]["recall_at_20"],
            "b6_final_decision_accuracy_fixture": policy_metrics["B6"]["final_decision_accuracy"],
            "b6_median_questions_fixture": policy_metrics["B6"]["median_questions_to_stable_top3"],
        },
        "accuracy_curves": {
            policy: _policy_curve(interactive, policy) for policy in ("B0", "B3", "B6")
        },
        "policy_table": [policy_metrics[policy] for policy in ("B0", "B1", "B2", "B3", "B4", "B6")],
        "ablation_table": [
            {"ablation": ablation_id, **metrics}
            for ablation_id, metrics in ablation["metrics"]["ablations"].items()
        ],
        "charts": charts,
        "prediction_count": prediction_count,
    }
    LATEST_ROOT.mkdir(parents=True, exist_ok=True)
    (LATEST_ROOT / "metrics.json").write_bytes(canonical_json_bytes(summary))
    with (LATEST_ROOT / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["metric", "value", "acceptance_eligible", "scope"])
        for name, value in summary["metrics"].items():
            writer.writerow([name, value, False, summary["claim_scope"]])
    markdown = [
        "# TRIAL-OPT Evaluation Summary",
        "",
        "**Status: PROVISIONAL ENGINEERING SMOKE — NOT ACCEPTANCE ELIGIBLE**",
        "",
        f"Claim scope: `{summary['claim_scope']}`. This is not clinical validation.",
        "",
        "## Generated metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for name, value in summary["metrics"].items():
        markdown.append(f"| `{name}` | {value} |")
    markdown.extend(["", "## Blocking reasons", ""])
    markdown.extend(f"- {reason}" for reason in summary["blocking_reasons"])
    markdown.extend(["", "Run IDs are recorded in `metrics.json`.", ""])
    (LATEST_ROOT / "summary.md").write_text("\n".join(markdown), encoding="utf-8")
    FRONTEND_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_SUMMARY.write_bytes(canonical_json_bytes(summary))
    print(
        orjson.dumps(
            {
                "metrics": str(LATEST_ROOT / "metrics.json"),
                "summary": str(LATEST_ROOT / "summary.md"),
                "charts": charts,
                "frontend": str(FRONTEND_SUMMARY),
                "acceptance_eligible": False,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
