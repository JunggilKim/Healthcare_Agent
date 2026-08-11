from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.engine.multi_trial_optimizer import (  # noqa: E402
    FullOptimizationState,
    candidate_policy_statistics,
)
from backend.app.evaluation.corpus import ReleaseCorpus, load_release_corpus  # noqa: E402
from backend.app.evaluation.interactive import (  # noqa: E402
    apply_benchmark_oracle_answer,
    build_optimization_state,
)
from backend.app.evaluation.models import (  # noqa: E402
    BenchmarkArtifact,
    MissingnessObservation,
    PatientWorld,
)
from backend.app.evaluation.policy_evidence import (  # noqa: E402
    DirectLLMChoiceStep,
    DirectLLMObservationRun,
    DirectLLMPolicyEvidence,
)
from backend.app.evaluation.retrieval_evidence import (  # noqa: E402
    CuratedRetrievalEvidence,
    load_curated_retrieval_evidence,
    validate_curated_retrieval_evidence,
)

MODEL_ID = "gemini-3.6-flash"
PROMPT_VERSION = "direct-question-policy-v1"


def _load_inputs(
    args: argparse.Namespace,
) -> tuple[
    bytes,
    BenchmarkArtifact,
    ReleaseCorpus,
    bytes,
    CuratedRetrievalEvidence,
]:
    benchmark_bytes = args.benchmark.read_bytes()
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(benchmark_bytes))
    corpus = load_release_corpus(
        compiled_paths=args.compiled_trials,
        raw_paths=args.raw_trials,
        review_paths=args.reviews,
    )
    retrieval_bytes = args.retrieval_evidence.read_bytes()
    retrieval = load_curated_retrieval_evidence(str(args.retrieval_evidence))
    validate_curated_retrieval_evidence(
        retrieval,
        benchmark=benchmark,
        benchmark_bytes=benchmark_bytes,
        corpus=corpus,
    )
    return benchmark_bytes, benchmark, corpus, retrieval_bytes, retrieval


def _selected_observations(
    benchmark: BenchmarkArtifact,
) -> list[MissingnessObservation]:
    selected = [
        item
        for item in benchmark.observations
        if item.split == "test" and item.rate == 0.4 and item.pattern == "REALISTIC"
    ]
    if not selected:
        raise RuntimeError("B5_HELD_OUT_OBSERVATIONS_MISSING")
    return selected


def _initial_progress(
    benchmark_sha256: str,
    retrieval_sha256: str,
    *,
    seed: int,
    max_questions: int,
    observations: list[MissingnessObservation],
) -> dict[str, Any]:
    return {
        "schema_version": "trial-opt-b5-progress-v1",
        "benchmark_sha256": benchmark_sha256,
        "retrieval_evidence_sha256": retrieval_sha256,
        "seed": seed,
        "max_questions": max_questions,
        "next_step": 0,
        "batch_job_names": [],
        "completed_observation_ids": [],
        "runs": {item.observation_id: [] for item in observations},
    }


def _load_progress(
    path: Path | None,
    *,
    benchmark_sha256: str,
    retrieval_sha256: str,
    seed: int,
    max_questions: int,
    observations: list[MissingnessObservation],
) -> dict[str, Any]:
    progress = (
        orjson.loads(path.read_bytes())
        if path is not None
        else _initial_progress(
            benchmark_sha256,
            retrieval_sha256,
            seed=seed,
            max_questions=max_questions,
            observations=observations,
        )
    )
    expected = {
        "benchmark_sha256": benchmark_sha256,
        "retrieval_evidence_sha256": retrieval_sha256,
        "seed": seed,
        "max_questions": max_questions,
    }
    if any(progress.get(key) != value for key, value in expected.items()):
        raise RuntimeError("B5_PROGRESS_PROVENANCE_MISMATCH")
    if set(progress.get("runs", {})) != {item.observation_id for item in observations}:
        raise RuntimeError("B5_PROGRESS_OBSERVATION_COVERAGE_MISMATCH")
    return progress


def _state_after_steps(
    *,
    observation: MissingnessObservation,
    world: PatientWorld,
    corpus: ReleaseCorpus,
    retrieval: CuratedRetrievalEvidence,
    progress_steps: list[dict[str, Any]],
    seed: int,
    max_questions: int,
) -> FullOptimizationState:
    query = next(item for item in retrieval.queries if item.world_id == observation.world_id)
    state = build_optimization_state(
        world=world,
        observation=observation,
        corpus=corpus,
        retrieval_scores=query.full_rrf_scores,
        exact_condition_matches=query.exact_condition_matches,
        detailed_nct_ids=query.detailed_nct_ids,
        max_questions=max_questions,
        run_key=f"{observation.observation_id}:B5:{seed}",
    )
    for raw_step in progress_steps:
        step = DirectLLMChoiceStep.model_validate(raw_step)
        candidates = candidate_policy_statistics(state)
        actual = sorted(item.candidate.slot_id for item in candidates)
        if sorted(step.candidate_slot_ids) != actual:
            raise RuntimeError("B5_PROGRESS_CANDIDATE_REPLAY_MISMATCH")
        selected = next(
            item.candidate for item in candidates if item.candidate.slot_id == step.selected_slot_id
        )
        apply_benchmark_oracle_answer(
            state,
            selected,
            world=world,
            observation=observation,
        )
    return state


def _prepare(args: argparse.Namespace) -> None:
    benchmark_bytes, benchmark, corpus, retrieval_bytes, retrieval = _load_inputs(args)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    seed = int(config["seed"])
    max_questions = int(config["max_questions"])
    observations = _selected_observations(benchmark)
    progress = _load_progress(
        args.progress,
        benchmark_sha256=hashlib.sha256(benchmark_bytes).hexdigest(),
        retrieval_sha256=hashlib.sha256(retrieval_bytes).hexdigest(),
        seed=seed,
        max_questions=max_questions,
        observations=observations,
    )
    step_index = int(progress["next_step"])
    if step_index >= max_questions:
        raise RuntimeError("B5_PROGRESS_ALREADY_AT_QUESTION_BUDGET")
    worlds = {item.world_id: item for item in benchmark.worlds}
    template = (REPOSITORY_ROOT / "prompts/direct_question_policy_v1.md").read_text(
        encoding="utf-8"
    )
    completed = set(progress["completed_observation_ids"])
    requests = []
    request_manifest: dict[str, Any] = {}
    for observation in observations:
        if observation.observation_id in completed:
            continue
        steps = progress["runs"][observation.observation_id]
        if len(steps) != step_index:
            raise RuntimeError("B5_PROGRESS_STEP_COUNT_MISMATCH")
        state = _state_after_steps(
            observation=observation,
            world=worlds[observation.world_id],
            corpus=corpus,
            retrieval=retrieval,
            progress_steps=steps,
            seed=seed,
            max_questions=max_questions,
        )
        candidates = candidate_policy_statistics(state)
        if not candidates:
            completed.add(observation.observation_id)
            continue
        ranked_state = [
            {
                "rank": rank,
                "nct_id": nct_id,
                "decision": state.aggregate.trial_evaluations[nct_id].decision.value,
                "critical_unknown_count": state.aggregate.trial_evaluations[
                    nct_id
                ].critical_unknown_count,
            }
            for rank, nct_id in enumerate(state.aggregate.ranked_nct_ids[:5], start=1)
        ]
        candidate_payload = [
            {
                "slot_id": item.candidate.slot_id,
                "action": item.candidate.action.value,
                "answer_type": item.candidate.answer_type,
                "affected_criterion_count": len(item.candidate.affected),
                "burden_class": state.slots[item.candidate.slot_id].burden_class,
                "question_ko": state.slots[item.candidate.slot_id].question_template_ko,
            }
            for item in candidates
        ]
        prompt = template.replace(
            "{ranked_state_json}", canonical_json_bytes(ranked_state).decode()
        ).replace("{candidate_questions_json}", canonical_json_bytes(candidate_payload).decode())
        candidate_ids = [item.candidate.slot_id for item in candidates]
        request_manifest[observation.observation_id] = {
            "candidate_slot_ids": candidate_ids,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        requests.append(
            {
                "id": observation.observation_id,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0,
                        "maxOutputTokens": 128,
                        "responseMimeType": "application/json",
                        "responseSchema": {
                            "type": "OBJECT",
                            "properties": {"selected_slot_id": {"type": "STRING"}},
                            "required": ["selected_slot_id"],
                        },
                    },
                },
            }
        )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    progress_before = {**progress, "completed_observation_ids": sorted(completed)}
    progress_path = output / "progress_before.json"
    progress_path.write_bytes(canonical_json_bytes(progress_before))
    request_path = output / "b5_requests.jsonl"
    request_path.write_bytes(
        b"\n".join(canonical_json_bytes(row) for row in requests) + (b"\n" if requests else b"")
    )
    manifest = {
        "schema_version": "trial-opt-b5-step-input-v1",
        "step_index": step_index,
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "progress_before_sha256": hashlib.sha256(progress_path.read_bytes()).hexdigest(),
        "request_jsonl_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
        "requests": request_manifest,
        "completed_observation_ids": sorted(completed),
    }
    (output / "input_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(
        orjson.dumps(
            {
                "output": str(output),
                "step_index": step_index,
                "requests": len(requests),
                "completed": len(completed),
            }
        ).decode()
    )


def _response_text(row: dict[str, Any]) -> str:
    if row.get("error"):
        raise ValueError(f"B5_BATCH_RESPONSE_ERROR:{row.get('id')}:{row['error']}")
    response = row.get("response")
    candidates = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError("B5_RESPONSE_CANDIDATE_INVALID")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    text = parts[0].get("text") if isinstance(parts, list) and parts else None
    if not isinstance(text, str) or not text:
        raise ValueError("B5_RESPONSE_TEXT_MISSING")
    return text


def _apply(args: argparse.Namespace) -> None:
    input_dir = args.input_dir.resolve()
    progress_path = input_dir / "progress_before.json"
    manifest = orjson.loads((input_dir / "input_manifest.json").read_bytes())
    if manifest["progress_before_sha256"] != hashlib.sha256(progress_path.read_bytes()).hexdigest():
        raise RuntimeError("B5_PROGRESS_INPUT_HASH_MISMATCH")
    progress = orjson.loads(progress_path.read_bytes())
    rows: list[dict[str, Any]] = []
    for path in args.responses:
        rows.extend(orjson.loads(raw) for raw in path.read_bytes().splitlines() if raw.strip())
    by_id = {row.get("id"): row for row in rows}
    if set(by_id) != set(manifest["requests"]) or len(by_id) != len(rows):
        raise RuntimeError("B5_RESPONSE_COVERAGE_MISMATCH")
    step_index = int(manifest["step_index"])
    for observation_id, request in manifest["requests"].items():
        text = _response_text(by_id[observation_id])
        payload = json.loads(text)
        if set(payload) != {"selected_slot_id"}:
            raise RuntimeError(f"B5_RESPONSE_SCHEMA_INVALID:{observation_id}")
        selected_slot = payload["selected_slot_id"]
        if selected_slot not in request["candidate_slot_ids"]:
            raise RuntimeError(f"B5_SELECTED_SLOT_OUTSIDE_CANDIDATES:{observation_id}")
        step = DirectLLMChoiceStep(
            step_index=step_index,
            candidate_slot_ids=request["candidate_slot_ids"],
            selected_slot_id=selected_slot,
            prompt_sha256=request["prompt_sha256"],
            response_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        progress["runs"][observation_id].append(step.model_dump(mode="json"))
    progress["batch_job_names"] = [*progress["batch_job_names"], args.batch_job_name]
    progress["completed_observation_ids"] = manifest["completed_observation_ids"]
    progress["next_step"] = step_index + 1
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(progress))
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "next_step": progress["next_step"],
                "responses": len(rows),
            }
        ).decode()
    )


def _finalize(args: argparse.Namespace) -> None:
    progress = orjson.loads(args.progress.read_bytes())
    all_ids = set(progress["runs"])
    completed = set(progress["completed_observation_ids"])
    if progress["next_step"] < progress["max_questions"] and completed != all_ids:
        raise RuntimeError("B5_PROGRESS_NOT_COMPLETE")
    evidence = DirectLLMPolicyEvidence(
        status="BATCH_COMPLETED",
        benchmark_sha256=progress["benchmark_sha256"],
        retrieval_evidence_sha256=progress["retrieval_evidence_sha256"],
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSION,
        random_seed=progress["seed"],
        batch_job_names=progress["batch_job_names"],
        completed_at=datetime.fromisoformat(args.completed_at),
        runs=[
            DirectLLMObservationRun(
                observation_id=observation_id,
                steps=[DirectLLMChoiceStep.model_validate(item) for item in steps],
            )
            for observation_id, steps in sorted(progress["runs"].items())
        ],
    )
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence.model_dump(mode="json")))
    print(orjson.dumps({"output": str(args.output), "runs": len(evidence.runs)}).decode())


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--compiled-trials", type=Path, action="append", required=True)
    parser.add_argument("--raw-trials", type=Path, action="append", required=True)
    parser.add_argument("--reviews", type=Path, action="append", required=True)
    parser.add_argument("--retrieval-evidence", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/eval.yaml"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare sequential paid B5 batch baseline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-step")
    _common(prepare)
    prepare.add_argument("--progress", type=Path)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)
    apply = subparsers.add_parser("apply-step")
    apply.add_argument("--input-dir", type=Path, required=True)
    apply.add_argument("--responses", type=Path, action="append", required=True)
    apply.add_argument("--batch-job-name", required=True)
    apply.add_argument("--output", type=Path, required=True)
    apply.set_defaults(handler=_apply)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--progress", type=Path, required=True)
    finalize.add_argument("--completed-at", required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.set_defaults(handler=_finalize)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
