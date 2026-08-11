from __future__ import annotations

import argparse
import sys
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.engine.trial_aggregator import is_trial_irrelevant  # noqa: E402
from backend.app.evaluation.corpus import load_release_corpus  # noqa: E402
from backend.app.evaluation.execution import eligibility_context_from_world  # noqa: E402
from backend.app.evaluation.models import BenchmarkArtifact  # noqa: E402
from backend.app.evaluation.retrieval_evidence import RetrievalSystemRun  # noqa: E402
from backend.app.retrieval.models import RetrievalResult  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize Dataset A retrieval baselines from recorded RetrievalResult JSON"
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--compiled-trials", type=Path, action="append", required=True)
    parser.add_argument("--raw-trials", type=Path, action="append", required=True)
    parser.add_argument("--reviews", type=Path, action="append", required=True)
    parser.add_argument("--recorded-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(args.benchmark.read_bytes()))
    corpus = load_release_corpus(
        compiled_paths=args.compiled_trials,
        raw_paths=args.raw_trials,
        review_paths=args.reviews,
    )
    payload = orjson.loads(args.recorded_results.read_bytes())
    if not isinstance(payload, list):
        raise RuntimeError("RECORDED_RETRIEVAL_RESULTS_SHAPE_INVALID")
    recorded = {}
    for row in payload:
        if not isinstance(row, dict) or not isinstance(row.get("world_id"), str):
            raise RuntimeError("RECORDED_RETRIEVAL_RESULT_ROW_INVALID")
        if row["world_id"] in recorded:
            raise RuntimeError("RECORDED_RETRIEVAL_RESULT_WORLD_DUPLICATE")
        recorded[row["world_id"]] = row
    test_worlds = {world.world_id: world for world in benchmark.worlds if world.split == "test"}
    if set(recorded) != set(test_worlds):
        raise RuntimeError("RECORDED_RETRIEVAL_RESULT_WORLD_COVERAGE_MISMATCH")
    runs = []
    for world_id, world in sorted(test_worlds.items()):
        row = recorded[world_id]
        if row.get("query_id") != f"query:{world_id}":
            raise RuntimeError(f"RECORDED_RETRIEVAL_QUERY_ID_MISMATCH:{world_id}")
        result = RetrievalResult.model_validate(row.get("retrieval_result"))
        candidates = result.ranked_candidates
        candidate_ids = {item.nct_id for item in candidates}
        if not candidate_ids.issubset(corpus.compiled_trials):
            raise RuntimeError(f"RECORDED_RETRIEVAL_TRIAL_OUTSIDE_CORPUS:{world_id}")
        context = eligibility_context_from_world(
            world.facts,
            world.conflict_slots,
            evaluation_date=world.evaluation_date,
            language=world.narrative_language,
        )
        confirmed_slots = {fact.slot_id for fact in context.facts}
        compiled_matches = {
            item.nct_id: bool(
                confirmed_slots
                & {
                    slot_id
                    for criterion in corpus.compiled_trials[item.nct_id].criteria
                    for slot_id in criterion.required_slots
                    if slot_id.startswith(("condition.", "diagnosis.", "pathology.histology"))
                }
            )
            for item in candidates
        }
        runs.append(
            RetrievalSystemRun(
                query_id=f"query:{world_id}",
                world_id=world_id,
                baseline_orders={
                    "ctgov_rank_only": [
                        item.nct_id
                        for item in sorted(
                            candidates, key=lambda item: (item.registry_rank, item.nct_id)
                        )
                    ],
                    "bm25_only": [
                        item.nct_id
                        for item in sorted(
                            candidates, key=lambda item: (item.bm25_rank, item.nct_id)
                        )
                    ],
                    "embedding_only": [
                        item.nct_id
                        for item in sorted(
                            candidates,
                            key=lambda item: (
                                item.embedding_rank if item.embedding_rank is not None else 10**9,
                                item.nct_id,
                            ),
                        )
                    ],
                    "ctgov_bm25_rrf": [
                        item.nct_id
                        for item in sorted(
                            candidates, key=lambda item: (-item.lexical_rrf, item.nct_id)
                        )
                    ],
                    "full_three_source_rrf": [item.nct_id for item in candidates],
                },
                full_rrf_scores={item.nct_id: item.retrieval_score for item in candidates},
                exact_condition_matches={
                    item.nct_id: item.exact_condition_match for item in candidates
                },
                compiled_condition_slot_matches=compiled_matches,
                irrelevance_decisions={
                    item.nct_id: is_trial_irrelevant(
                        retrieval_score=item.retrieval_score,
                        exact_condition_match=item.exact_condition_match,
                        compiled_trial=corpus.compiled_trials[item.nct_id],
                        facts=context.facts,
                    )
                    for item in candidates
                },
                detailed_nct_ids=result.selected_for_compilation,
            )
        )
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes([item.model_dump(mode="json") for item in runs]))
    print(orjson.dumps({"output": str(args.output), "queries": len(runs)}).decode())


if __name__ == "__main__":
    main()
