from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, cast

import matplotlib
import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "trial-opt-20260811"
import matplotlib.pyplot as plt  # noqa: E402

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.performance_evidence import (  # noqa: E402
    ReleasePerformanceEvidence,
    performance_acceptance_metrics,
)

LATEST_ROOT = Path("artifacts/eval/latest")
FRONTEND_SUMMARY = Path("frontend/public/eval/summary.json")
PERFORMANCE_EVIDENCE = Path("artifacts/eval/performance/evidence.json")


def _load_suite(name: str) -> dict[str, Any]:
    path = LATEST_ROOT / "suites" / f"{name}.json"
    if not path.is_file():
        raise RuntimeError(f"MISSING_EVAL_SUITE:{name}")
    return cast(dict[str, Any], orjson.loads(path.read_bytes()))


def _policy_curve(interactive: dict[str, Any], policy: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        interactive["metrics"]["policies"][policy]["accuracy_by_question"],
    )


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
    maximum_questions = max(item["questions"] for item in _policy_curve(interactive, "B6"))
    plt.xticks(range(maximum_questions + 1))
    plt.xlabel("Questions")
    plt.ylabel("Decision accuracy")
    title = (
        "Dataset A held-out: accuracy vs questions"
        if interactive["metrics"].get("acceptance_eligible")
        else "S004 fixture smoke: accuracy vs questions"
    )
    plt.title(title)
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


def _shared_provenance(suites: dict[str, dict[str, Any]]) -> dict[str, Any]:
    git_shas = {document["metadata"]["git_sha"] for document in suites.values()}
    config_hashes = {document["metadata"]["config_hash"] for document in suites.values()}
    seeds = {document["metadata"]["random_seed"] for document in suites.values()}
    if len(git_shas) != 1 or len(config_hashes) != 1 or len(seeds) != 1:
        raise RuntimeError("EVALUATION_SUITE_PROVENANCE_MISMATCH")
    return {
        "source_git_sha": git_shas.pop(),
        "config_hash": config_hashes.pop(),
        "random_seed": seeds.pop(),
    }


def _load_performance_evidence() -> ReleasePerformanceEvidence | None:
    if not PERFORMANCE_EVIDENCE.is_file():
        return None
    return ReleasePerformanceEvidence.model_validate(
        orjson.loads(PERFORMANCE_EVIDENCE.read_bytes())
    )


def _release_acceptance_metrics(
    suites: dict[str, dict[str, Any]],
    performance: ReleasePerformanceEvidence,
) -> dict[str, Any]:
    criterion = suites["criterion"]["metrics"]
    retrieval = suites["retrieval"]["metrics"]
    interactive = suites["interactive"]["metrics"]
    safety = criterion["safety_metrics"]
    protocol = criterion["protocol_metrics"]
    b3 = interactive["policies"]["B3"]
    b6 = interactive["policies"]["B6"]
    performance_metrics = performance_acceptance_metrics(performance)
    return {
        "criterion_macro_f1": criterion["criterion_metrics"]["macro_f1"],
        "hard_fail_recall": criterion["hard_fail_recall"],
        "false_pre_screen_pass_rate": criterion["false_pre_screen_pass_rate"],
        "evidence_precision": criterion["evidence_precision"],
        "retrieval_recall_at_20": retrieval["baselines"]["full_three_source_rrf"]["recall_at_20"],
        "bm25_recall_at_20": retrieval["baselines"]["bm25_only"]["recall_at_20"],
        "exact_condition_irrelevance_exclusion_count": retrieval[
            "exact_condition_irrelevance_exclusion_count"
        ],
        **safety,
        **protocol,
        "median_questions_to_stable_top3_40_realistic": b6["median_questions_to_stable_top3"],
        "b3_median_questions_40_realistic": b3["median_questions_to_stable_top3"],
        "decision_accuracy_after_3": interactive["decision_accuracy_after_3"],
        "b3_decision_accuracy_after_3": interactive["b3_decision_accuracy_after_3"],
        "question_count_statistically_tied_with_b3": interactive["question_count_statistical_test"][
            "statistically_tied"
        ],
        "max_policy_questions": interactive["max_policy_questions"],
        "hard_question_budget": interactive["hard_question_budget"],
        "repeat_seed_identical": interactive["repeat_seed_identical"],
        **performance_metrics,
    }


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
    provenance = _shared_provenance(suites)
    performance = _load_performance_evidence()
    suite_status = {
        name: document["metrics"].get("acceptance_eligible") is True
        for name, document in suites.items()
    }
    proof_baselines = criterion["metrics"].get("proof_baselines", {})
    paid_proof_baselines_complete = all(
        proof_baselines.get(name, {}).get("status") == "BATCH_COMPLETED" for name in ("P0", "P1")
    )
    performance_bound = (
        performance is not None and performance.source_git_sha == provenance["source_git_sha"]
    )
    acceptance_eligible = (
        all(suite_status.values()) and paid_proof_baselines_complete and performance_bound
    )
    blocking_reasons = [
        f"{name} suite is not release acceptance eligible."
        for name, ready in suite_status.items()
        if not ready
    ]
    if not paid_proof_baselines_complete:
        blocking_reasons.append("Paid proof baselines P0 and P1 are incomplete.")
    if performance is None:
        blocking_reasons.append("Commit-bound controlled performance evidence is missing.")
    elif not performance_bound:
        blocking_reasons.append("Performance evidence git SHA does not match evaluation suites.")

    if acceptance_eligible:
        assert performance is not None
        generated_metrics = _release_acceptance_metrics(suites, performance)
        claim_scope = (
            "project-created synthetic Dataset A with protocol-text adjudication by project "
            "reviewers"
        )
    else:
        generated_metrics = {
            "criterion_macro_f1_self_consistency": criterion["metrics"]["criterion_metrics"][
                "macro_f1"
            ],
            "false_pre_screen_pass_rate_fixture": criterion["metrics"][
                "false_pre_screen_pass_rate"
            ],
            "retrieval_recall_at_20_proxy": retrieval["metrics"]["baselines"][
                "full_three_source_rrf"
            ]["recall_at_20"],
            "b6_final_decision_accuracy_fixture": policy_metrics["B6"]["final_decision_accuracy"],
            "b6_median_questions_fixture": policy_metrics["B6"]["median_questions_to_stable_top3"],
        }
        proof_p3 = proof_baselines.get("P3", {})
        if "unsupported_hard_decision_rate" in proof_p3:
            generated_metrics["unsupported_hard_decision_rate_fixture"] = proof_p3[
                "unsupported_hard_decision_rate"
            ]
        claim_scope = "project-created S004 structured fixture engineering smoke"

    policy_order = ["B0", "B1", "B2", "B3", "B4", "B5", "B6"]
    summary = {
        "schema_version": "trial-opt-eval-summary-v1",
        "claim_scope": claim_scope,
        "acceptance_eligible": acceptance_eligible,
        "clinical_validation": False,
        "blocking_reasons": blocking_reasons,
        **provenance,
        "run_ids": {name: document["metadata"]["run_id"] for name, document in suites.items()},
        "metrics": generated_metrics,
        "acceptance_metrics": generated_metrics if acceptance_eligible else {},
        "accuracy_curves": {
            policy: _policy_curve(interactive, policy) for policy in ("B0", "B3", "B6")
        },
        "policy_table": [
            policy_metrics[policy] for policy in policy_order if policy in policy_metrics
        ],
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
            writer.writerow([name, value, acceptance_eligible, summary["claim_scope"]])
    status = (
        "RELEASE EVALUATION — ACCEPTANCE ARTIFACT COMPLETE"
        if acceptance_eligible
        else "PROVISIONAL ENGINEERING EVIDENCE — NOT ACCEPTANCE ELIGIBLE"
    )
    markdown = [
        "# TRIAL-OPT Evaluation Summary",
        "",
        f"**Status: {status}**",
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
    if blocking_reasons:
        markdown.extend(["", "## Blocking reasons", ""])
        markdown.extend(f"- {reason}" for reason in blocking_reasons)
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
                "acceptance_eligible": acceptance_eligible,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
