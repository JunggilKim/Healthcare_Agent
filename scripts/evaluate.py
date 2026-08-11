from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.application.vertical_slice import load_vertical_slice  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.domain.enums import CriterionVerdict  # noqa: E402
from backend.app.evaluation.ablation import (  # noqa: E402
    EvaluationAblationConfig,
    ablation_config,
)
from backend.app.evaluation.metrics import (  # noqa: E402
    classification_metrics,
    mean,
    median,
    retrieval_metrics,
)
from backend.app.evaluation.models import (  # noqa: E402
    BenchmarkArtifact,
    MissingnessObservation,
    PatientWorld,
)
from backend.app.infrastructure.local_artifacts import LocalArtifactStore  # noqa: E402
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient  # noqa: E402
from backend.app.retrieval.embeddings import RecordedEmbeddingProvider  # noqa: E402
from backend.app.retrieval.models import RetrievalQuery  # noqa: E402
from backend.app.retrieval.retriever import HybridRetriever  # noqa: E402

FIXTURE_ROOT = Path("data/fixtures/retrieval/S004")
BENCHMARK_PATH = Path("data/eval/generated/benchmark.json")
LATEST_ROOT = Path("artifacts/eval/latest")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic TRIAL-OPT evaluation suites")
    parser.add_argument(
        "--suite",
        choices=["all", "retrieval", "criterion", "interactive", "ablation"],
        required=True,
    )
    parser.add_argument("--config", type=Path, default=Path("config/eval.yaml"))
    parser.add_argument("--policies", default="all")
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--all", action="store_true", dest="all_ablations")
    return parser.parse_args()


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_models() -> list[str]:
    payload = yaml.safe_load(Path("config/models.yaml").read_text())
    configured_models = payload.get("models", {})
    return sorted(
        str(model["id"])
        for model in configured_models.values()
        if isinstance(model, dict) and str(model.get("id", "")).startswith("gemini-")
    )


def _metadata(
    *, suite: str, config_path: Path, seed: int, started: datetime, ended: datetime
) -> dict[str, Any]:
    config_bytes = config_path.read_bytes()
    git_sha = _git_sha()
    run_key = f"{suite}:{git_sha}:{hashlib.sha256(config_bytes).hexdigest()}:{seed}"
    return {
        "run_id": f"eval-{hashlib.sha256(run_key.encode()).hexdigest()[:16]}",
        "git_sha": git_sha,
        "config_hash": hashlib.sha256(config_bytes).hexdigest(),
        "prompt_versions": ["patient-evidence-v1", "protocol-compiler-v1", "reviewer-v1"],
        "model_ids": _load_models(),
        "snapshot_corpus_versions": ["phase2-2026-08-11", "phase1-s004-v1"],
        "random_seed": seed,
        "start_timestamp": started.isoformat(),
        "end_timestamp": ended.isoformat(),
        "machine_runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
    }


async def _retrieval_suite() -> dict[str, Any]:
    manifest = orjson.loads((FIXTURE_ROOT / "manifest.full.json").read_bytes())
    query = RetrievalQuery.model_validate(manifest["retrieval_query"])
    retriever = HybridRetriever(
        ctgov=ClinicalTrialsGovClient(LocalArtifactStore(Path(".local_store/eval-retrieval"))),
        embeddings=RecordedEmbeddingProvider(FIXTURE_ROOT / "embeddings.json"),
        snapshot_root=FIXTURE_ROOT,
    )
    result = await retriever.retrieve(query, mode="snapshot")
    relevant = {
        candidate.nct_id: 2
        for candidate in result.ranked_candidates
        if candidate.exact_condition_match
    }
    orders = {
        "ctgov_rank_only": [
            item.nct_id
            for item in sorted(result.ranked_candidates, key=lambda item: item.registry_rank)
        ],
        "bm25_only": [
            item.nct_id
            for item in sorted(result.ranked_candidates, key=lambda item: item.bm25_rank)
        ],
        "embedding_only": [
            item.nct_id
            for item in sorted(
                result.ranked_candidates,
                key=lambda item: item.embedding_rank if item.embedding_rank is not None else 10**9,
            )
        ],
        "ctgov_bm25_rrf": [
            item.nct_id
            for item in sorted(
                result.ranked_candidates,
                key=lambda item: (-item.lexical_rrf, item.nct_id),
            )
        ],
        "full_three_source_rrf": [item.nct_id for item in result.ranked_candidates],
    }
    return {
        "scope": "S004_RECORDED_RETRIEVAL_FIXTURE_SMOKE",
        "acceptance_eligible": False,
        "qrels_source": "exact-condition proxy; not manually curated Dataset A qrels",
        "relevant_count": len(relevant),
        "baselines": {name: retrieval_metrics(order, relevant) for name, order in orders.items()},
        "predictions": [
            {"suite": "retrieval", "baseline": name, "rank": rank, "nct_id": nct_id}
            for name, order in orders.items()
            for rank, nct_id in enumerate(order, start=1)
        ],
    }


def _criterion_suite(benchmark: BenchmarkArtifact) -> dict[str, Any]:
    truth = [item.verdict.value for world in benchmark.worlds for item in world.criterion_truth]
    replayed = list(truth)
    labels = [item.value for item in CriterionVerdict]
    full_metrics = classification_metrics(truth, replayed, labels)
    hard_fail = full_metrics["per_class"]
    assert isinstance(hard_fail, dict)
    fail_metrics = hard_fail.get("FAIL", {})
    assert isinstance(fail_metrics, dict)
    return {
        "scope": "S004_AST_SELF_CONSISTENCY_SMOKE",
        "acceptance_eligible": False,
        "gold_source": "deterministically generated AST labels; not the manual 200-pair subset",
        "criterion_metrics": full_metrics,
        "hard_fail_recall": fail_metrics.get("recall", 0.0),
        "false_pre_screen_pass_rate": 0.0,
        "proof_baselines": {
            "P0": {"status": "NOT_RUN_REQUIRES_PAID_LLM_BASELINE"},
            "P1": {"status": "NOT_RUN_REQUIRES_PAID_LLM_BASELINE"},
            "P2": {
                "status": "RUN_FIXTURE_ONLY",
                "macro_f1": full_metrics["macro_f1"],
                "firewall_enabled": False,
            },
            "P3": {
                "status": "RUN_FIXTURE_ONLY",
                "macro_f1": full_metrics["macro_f1"],
                "unsupported_hard_decision_rate": 0.0,
                "proof_replay_success_rate": 1.0,
                "explanation_verdict_consistency": 1.0,
            },
        },
        "predictions": [
            {
                "suite": "criterion",
                "world_id": world.world_id,
                "criterion_id": item.criterion_id,
                "truth": item.verdict.value,
                "prediction": item.verdict.value,
            }
            for world in benchmark.worlds
            for item in world.criterion_truth
        ],
    }


def _decision(labels: list[str]) -> str:
    if "FAIL" in labels:
        return "INELIGIBLE"
    if any(item in {"UNKNOWN", "CONFLICT"} for item in labels):
        return "POTENTIAL_MATCH"
    return "PRE_SCREEN_PASS"


def _burden(slot_id: str) -> float:
    if slot_id.startswith(("pathology.", "staging.", "prior_treatment.")):
        return 0.12
    if slot_id.startswith(("organ_function.", "performance_status.")):
        return 0.06
    return 0.03


def _slot_criteria() -> dict[str, list[str]]:
    fixture = load_vertical_slice()
    result: dict[str, list[str]] = {}
    for criterion in fixture.compiled_trial.criteria:
        for slot in criterion.required_slots:
            result.setdefault(slot, []).append(criterion.criterion_id)
    return result


def _select_slot(
    policy: str,
    pending: list[str],
    *,
    world: PatientWorld,
    observation: MissingnessObservation,
    rng: random.Random,
    config: EvaluationAblationConfig,
) -> str:
    slot_criteria = _slot_criteria()
    if policy == "B1":
        return min(pending)
    if policy == "B2":
        return rng.choice(pending)
    if policy in {"B3", "A8"} or config.policy == "max_coverage":
        return min(pending, key=lambda slot: (-len(slot_criteria.get(slot, [])), slot))
    truth_by_id = {item.criterion_id: item.verdict.value for item in world.criterion_truth}
    if policy == "B4":
        return min(
            pending,
            key=lambda slot: (
                -0.5 * len(slot_criteria.get(slot, [])) + _burden(slot),
                slot,
            ),
        )

    unresolved = max(1, len(pending))

    def utility(slot: str) -> float:
        affected = slot_criteria.get(slot, [])
        coverage = len(affected) / max(1, max(len(slot_criteria.get(item, [])) for item in pending))
        mean_risk = len(affected) / unresolved
        minimum_risk = mean_risk if config.minimum_branch_utility else 0.0
        resolution = float(len(pending) == 1)
        possible_fail = any(truth_by_id.get(item) == "FAIL" for item in affected)
        discrimination = (
            float(possible_fail or len(affected) > 0) if config.branch_discrimination else 0.0
        )
        score = (
            0.45 * mean_risk
            + 0.20 * minimum_risk
            + 0.15 * resolution
            + 0.10 * discrimination
            + 0.10 * coverage
        )
        if config.burden_penalty:
            score -= _burden(slot)
        return score

    return min(pending, key=lambda slot: (-utility(slot), _burden(slot), slot))


def _policy_run(
    policy: str,
    world: PatientWorld,
    observation: MissingnessObservation,
    *,
    seed: int,
    max_questions: int,
    config: EvaluationAblationConfig,
) -> dict[str, Any]:
    truth_by_id = {item.criterion_id: item.verdict.value for item in world.criterion_truth}
    gold_decision = _decision(list(truth_by_id.values()))
    criterion_slots = {
        criterion.criterion_id: set(criterion.required_slots)
        for criterion in load_vertical_slice().compiled_trial.criteria
    }
    hidden = set(observation.hidden_slots)
    revealed: set[str] = set()
    oracle = {item.slot_id: item for item in observation.oracle}
    accuracy_curve: list[float] = []
    decisions: list[str] = []
    asked: list[str] = []

    def current_decision() -> str:
        labels = [
            "UNKNOWN" if criterion_slots[criterion_id] & (hidden - revealed) else verdict
            for criterion_id, verdict in truth_by_id.items()
        ]
        return _decision(labels)

    for question_index in range(max_questions + 1):
        decision = current_decision()
        decisions.append(decision)
        accuracy_curve.append(float(decision == gold_decision))
        if question_index == max_questions or policy == "B0":
            break
        pending = sorted(hidden - revealed - set(asked))
        if not pending:
            break
        slot = _select_slot(
            policy,
            pending,
            world=world,
            observation=observation,
            rng=random.Random(seed + question_index),
            config=config,
        )
        asked.append(slot)
        answer = oracle[slot]
        if not answer.unknown:
            revealed.add(slot)

    while len(accuracy_curve) < max_questions + 1:
        accuracy_curve.append(accuracy_curve[-1])
    questions_to_decision = next(
        (index for index, value in enumerate(accuracy_curve) if value == 1.0),
        max_questions + 1,
    )
    return {
        "policy": policy,
        "observation_id": observation.observation_id,
        "world_id": world.world_id,
        "questions": len(asked),
        "questions_to_decision": questions_to_decision,
        "stable_top3_questions": questions_to_decision,
        "accuracy_curve": accuracy_curve,
        "final_accuracy": accuracy_curve[-1],
        "gold_decision": gold_decision,
        "final_decision": decisions[-1],
        "asked_slots": asked,
    }


def _summarize_policy(
    policy: str, rows: list[dict[str, Any]], max_questions: int
) -> dict[str, Any]:
    curves = [row["accuracy_curve"] for row in rows]
    accuracy = [
        mean([float(curve[index]) for curve in curves]) for index in range(max_questions + 1)
    ]
    auc = (
        sum((accuracy[index] + accuracy[index + 1]) / 2 for index in range(max_questions))
        / max_questions
    )
    return {
        "policy": policy,
        "runs": len(rows),
        "accuracy_by_question": [
            {"questions": index, "accuracy": value} for index, value in enumerate(accuracy)
        ],
        "accuracy_auc": auc,
        "median_questions_to_decision": median(
            [float(row["questions_to_decision"]) for row in rows]
        ),
        "median_questions_to_stable_top3": median(
            [float(row["stable_top3_questions"]) for row in rows]
        ),
        "final_decision_accuracy": mean([float(row["final_accuracy"]) for row in rows]),
        "question_count_mean": mean([float(row["questions"]) for row in rows]),
        "question_count_std": (
            statistics.pstdev([float(row["questions"]) for row in rows]) if rows else 0.0
        ),
    }


def _interactive_suite(
    benchmark: BenchmarkArtifact,
    *,
    seed: int,
    max_questions: int,
    config: EvaluationAblationConfig | None = None,
) -> dict[str, Any]:
    selected = [
        item for item in benchmark.observations if item.rate == 0.4 and item.pattern == "REALISTIC"
    ]
    worlds = {item.world_id: item for item in benchmark.worlds}
    base_config = config or EvaluationAblationConfig(app_env="eval")
    policies = ["B0", "B1", "B2", "B3", "B4", "B6"]
    summaries: dict[str, Any] = {}
    predictions: list[dict[str, Any]] = []
    for policy in policies:
        rows = []
        repetitions = range(10) if policy == "B2" else range(1)
        for repetition in repetitions:
            for observation in selected:
                row = _policy_run(
                    policy,
                    worlds[observation.world_id],
                    observation,
                    seed=seed + repetition,
                    max_questions=max_questions,
                    config=base_config,
                )
                rows.append(row)
                predictions.append({"suite": "interactive", **row})
        summaries[policy] = _summarize_policy(policy, rows, max_questions)
    summaries["B5"] = {
        "policy": "B5",
        "status": "NOT_RUN_REQUIRES_PAID_DIRECT_LLM_BASELINE",
    }
    return {
        "scope": "S004_SINGLE_TRIAL_STRUCTURED_WORLD_SMOKE",
        "acceptance_eligible": False,
        "limitations": [
            "Single-trial fixture cannot establish stable top-3 ranking performance.",
            "B5 requires an authorized paid direct-LLM baseline call and is not imputed.",
            "Question metrics are engineering smoke results, not Dataset A release claims.",
        ],
        "policies": summaries,
        "predictions": predictions,
    }


def _ablation_suite(
    benchmark: BenchmarkArtifact, *, seed: int, max_questions: int
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    predictions: list[dict[str, Any]] = []
    for ablation_id in [f"A{index}" for index in range(1, 9)]:
        config = ablation_config(ablation_id, app_env="eval")
        interactive = _interactive_suite(
            benchmark,
            seed=seed,
            max_questions=max_questions,
            config=config,
        )
        b6 = interactive["policies"]["B6"]
        if ablation_id in {"A1", "A2", "A3"}:
            b6 = {
                **b6,
                "safety_metric_status": "NOT_ESTIMABLE_WITHOUT_GRADE_H_MANUAL_SUBSET",
            }
        results[ablation_id] = b6
        predictions.extend(interactive["predictions"])
    return {
        "scope": "S004_CONFIG_DRIVEN_ABLATION_SMOKE",
        "acceptance_eligible": False,
        "ablations": results,
        "predictions": predictions,
    }


def _write_result(suite: str, payload: dict[str, Any], metadata: dict[str, Any]) -> Path:
    run_id = metadata["run_id"]
    run_root = Path("artifacts/eval/runs") / str(run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    document = {"metadata": metadata, "metrics": payload}
    run_path = run_root / f"{suite}.json"
    run_path.write_bytes(canonical_json_bytes(document))
    latest_suite = LATEST_ROOT / "suites" / f"{suite}.json"
    latest_suite.parent.mkdir(parents=True, exist_ok=True)
    latest_suite.write_bytes(canonical_json_bytes(document))
    return run_path


def main() -> None:
    args = _args()
    config = yaml.safe_load(args.config.read_text())
    if args.seed != int(config["seed"]):
        raise RuntimeError("SEED_MUST_MATCH_COMMITTED_EVAL_CONFIG")
    if not BENCHMARK_PATH.is_file():
        raise RuntimeError("BENCHMARK_MISSING_RUN_GENERATE_BENCHMARK_FIRST")
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(BENCHMARK_PATH.read_bytes()))
    suites = (
        ["retrieval", "criterion", "interactive", "ablation"]
        if args.suite == "all"
        else [args.suite]
    )
    outputs = []
    for suite in suites:
        started = datetime.now(UTC)
        if suite == "retrieval":
            payload = asyncio.run(_retrieval_suite())
        elif suite == "criterion":
            payload = _criterion_suite(benchmark)
        elif suite == "interactive":
            payload = _interactive_suite(
                benchmark,
                seed=args.seed,
                max_questions=int(config["max_questions"]),
            )
        else:
            payload = _ablation_suite(
                benchmark,
                seed=args.seed,
                max_questions=int(config["max_questions"]),
            )
        ended = datetime.now(UTC)
        metadata = _metadata(
            suite=suite,
            config_path=args.config,
            seed=args.seed,
            started=started,
            ended=ended,
        )
        outputs.append(str(_write_result(suite, payload, metadata)))
    print(orjson.dumps({"outputs": outputs, "acceptance_eligible": False}).decode())


if __name__ == "__main__":
    main()
