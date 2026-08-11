from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.corpus import load_release_corpus  # noqa: E402
from backend.app.evaluation.models import BenchmarkArtifact  # noqa: E402
from backend.app.evaluation.retrieval_evidence import (  # noqa: E402
    CuratedRetrievalEvidence,
    CuratedRetrievalQuery,
    RetrievalRelevanceAssignment,
    RetrievalRelevanceReview,
    RetrievalSystemRun,
    retrieval_assignment_hash,
)


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\n".join(canonical_json_bytes(row.model_dump(mode="json")) for row in rows) + b"\n"
    )


def _load_jsonl(path: Path, model: type[Any]) -> list[Any]:
    result = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            result.append(model.model_validate(orjson.loads(raw)))
        except Exception as exc:
            raise ValueError(f"RETRIEVAL_REVIEW_JSONL_INVALID:{line_number}:{exc}") from exc
    return result


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--compiled-trials", type=Path, action="append", required=True)
    parser.add_argument("--raw-trials", type=Path, action="append", required=True)
    parser.add_argument("--reviews", type=Path, action="append", required=True)


def _prepare(args: argparse.Namespace) -> None:
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(args.benchmark.read_bytes()))
    if not benchmark.acceptance_eligible:
        raise RuntimeError("RETRIEVAL_REVIEW_REQUIRES_RELEASE_DATASET_A")
    corpus = load_release_corpus(
        compiled_paths=args.compiled_trials,
        raw_paths=args.raw_trials,
        review_paths=args.reviews,
    )
    assignments = []
    for world in sorted(
        (item for item in benchmark.worlds if item.split == "test"),
        key=lambda item: item.world_id,
    ):
        for nct_id, trial in sorted(corpus.raw_trials.items()):
            record_id = (
                f"ret_{hashlib.sha256(f'{world.world_id}:{nct_id}'.encode()).hexdigest()[:24]}"
            )
            draft = RetrievalRelevanceAssignment.model_construct(
                record_id=record_id,
                query_id=f"query:{world.world_id}",
                world_id=world.world_id,
                patient_narrative=world.narrative,
                patient_facts=world.facts,
                candidate_nct_id=nct_id,
                candidate_title=trial.official_title or trial.brief_title,
                candidate_conditions=trial.conditions,
                candidate_summary=trial.brief_summary,
                assignment_hash="",
            )
            assignments.append(
                RetrievalRelevanceAssignment.model_validate(
                    {
                        **draft.model_dump(mode="json", exclude={"assignment_hash"}),
                        "assignment_hash": retrieval_assignment_hash(draft),
                    }
                )
            )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    assignment_path = output / "retrieval_review_assignments.jsonl"
    _write_jsonl(assignment_path, assignments)
    manifest = {
        "schema_version": "trial-opt-retrieval-review-manifest-v1",
        "status": "READY_FOR_BLINDED_REVIEW",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "assignment_jsonl_sha256": hashlib.sha256(assignment_path.read_bytes()).hexdigest(),
        "assignment_count": len(assignments),
        "query_count": len({item.query_id for item in assignments}),
        "candidate_trials_per_query": len(corpus.raw_trials),
        "blinding_contract": "system rankings and target trial IDs are absent from assignments",
    }
    (output / "assignment_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), **manifest}).decode())


def _finalize(args: argparse.Namespace) -> None:
    benchmark_bytes = args.benchmark.read_bytes()
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(benchmark_bytes))
    corpus = load_release_corpus(
        compiled_paths=args.compiled_trials,
        raw_paths=args.raw_trials,
        review_paths=args.reviews,
    )
    assignments = _load_jsonl(args.assignments, RetrievalRelevanceAssignment)
    reviews = _load_jsonl(args.relevance_reviews, RetrievalRelevanceReview)
    assignment_by_id = {item.record_id: item for item in assignments}
    review_by_id = {item.record_id: item for item in reviews}
    if len(assignment_by_id) != len(assignments) or len(review_by_id) != len(reviews):
        raise RuntimeError("RETRIEVAL_REVIEW_RECORD_DUPLICATE")
    if set(review_by_id) != set(assignment_by_id):
        raise RuntimeError("RETRIEVAL_REVIEW_COVERAGE_MISMATCH")
    for record_id, review in review_by_id.items():
        if review.assignment_hash != assignment_by_id[record_id].assignment_hash:
            raise RuntimeError(f"RETRIEVAL_REVIEW_ASSIGNMENT_HASH_MISMATCH:{record_id}")
    run_payload = orjson.loads(args.system_runs.read_bytes())
    if not isinstance(run_payload, list):
        raise RuntimeError("RETRIEVAL_SYSTEM_RUNS_SHAPE_INVALID")
    runs = [RetrievalSystemRun.model_validate(item) for item in run_payload]
    run_by_world = {item.world_id: item for item in runs}
    test_worlds = {item.world_id: item for item in benchmark.worlds if item.split == "test"}
    if len(run_by_world) != len(runs) or set(run_by_world) != set(test_worlds):
        raise RuntimeError("RETRIEVAL_SYSTEM_RUN_WORLD_COVERAGE_MISMATCH")
    assignments_by_world: dict[str, list[RetrievalRelevanceAssignment]] = {}
    for assignment in assignments:
        assignments_by_world.setdefault(assignment.world_id, []).append(assignment)
    queries = []
    for world_id, world in sorted(test_worlds.items()):
        run = run_by_world[world_id]
        if run.query_id != f"query:{world_id}":
            raise RuntimeError(f"RETRIEVAL_SYSTEM_RUN_QUERY_ID_MISMATCH:{world_id}")
        world_assignments = assignments_by_world.get(world_id, [])
        if {item.candidate_nct_id for item in world_assignments} != set(corpus.raw_trials):
            raise RuntimeError(f"RETRIEVAL_REVIEW_CORPUS_COVERAGE_MISMATCH:{world_id}")
        world_reviews = [review_by_id[item.record_id] for item in world_assignments]
        queries.append(
            CuratedRetrievalQuery(
                query_id=f"query:{world_id}",
                world_id=world_id,
                target_nct_id=world.nct_id,
                qrels={
                    assignment.candidate_nct_id: review_by_id[assignment.record_id].relevance
                    for assignment in world_assignments
                },
                baseline_orders=run.baseline_orders,
                full_rrf_scores=run.full_rrf_scores,
                exact_condition_matches=run.exact_condition_matches,
                compiled_condition_slot_matches=run.compiled_condition_slot_matches,
                irrelevance_decisions=run.irrelevance_decisions,
                detailed_nct_ids=run.detailed_nct_ids,
                reviewer_labels=sorted({item.reviewer_label for item in world_reviews}),
                adjudicated_at=max(item.reviewed_at for item in world_reviews),
            )
        )
    evidence = CuratedRetrievalEvidence(
        status="CURATED_ADJUDICATED",
        benchmark_sha256=hashlib.sha256(benchmark_bytes).hexdigest(),
        corpus_trial_hashes={
            nct_id: trial.content_hash for nct_id, trial in corpus.compiled_trials.items()
        },
        retrieval_config_sha256=hashlib.sha256(args.retrieval_config.read_bytes()).hexdigest(),
        query_artifact_sha256=hashlib.sha256(args.query_artifact.read_bytes()).hexdigest(),
        snapshot_manifest_sha256=hashlib.sha256(args.snapshot_manifest.read_bytes()).hexdigest(),
        run_id=args.run_id,
        git_sha=args.run_git_sha,
        queries=queries,
    )
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence.model_dump(mode="json")))
    print(orjson.dumps({"output": str(args.output), "queries": len(queries)}).decode())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare blinded Dataset A retrieval qrels and finalize system evidence"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-review")
    _common(prepare)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)
    finalize = subparsers.add_parser("finalize")
    _common(finalize)
    finalize.add_argument("--assignments", type=Path, required=True)
    finalize.add_argument("--relevance-reviews", type=Path, required=True)
    finalize.add_argument("--system-runs", type=Path, required=True)
    finalize.add_argument("--retrieval-config", type=Path, required=True)
    finalize.add_argument("--query-artifact", type=Path, required=True)
    finalize.add_argument("--snapshot-manifest", type=Path, required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--run-git-sha", required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.set_defaults(handler=_finalize)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
