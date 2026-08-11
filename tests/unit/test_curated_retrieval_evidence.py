from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.evaluation.corpus import build_release_corpus
from backend.app.evaluation.retrieval_evidence import (
    CuratedRetrievalEvidence,
    CuratedRetrievalQuery,
    evaluate_curated_retrieval,
)
from backend.app.evaluation.worlds import generate_fixture_benchmark

BASELINES = {
    "ctgov_rank_only",
    "bm25_only",
    "embedding_only",
    "ctgov_bm25_rrf",
    "full_three_source_rrf",
}


def _query(world_id: str) -> CuratedRetrievalQuery:
    nct_id = "NCT05239624"
    return CuratedRetrievalQuery(
        query_id=f"query:{world_id}",
        world_id=world_id,
        target_nct_id=nct_id,
        qrels={nct_id: 2},
        baseline_orders={name: [nct_id] for name in BASELINES},
        full_rrf_scores={nct_id: 1.0},
        exact_condition_matches={nct_id: True},
        compiled_condition_slot_matches={nct_id: True},
        irrelevance_decisions={nct_id: False},
        detailed_nct_ids=[nct_id],
        reviewer_labels=["project-reviewer-a"],
        adjudicated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def test_curated_retrieval_evidence_covers_every_held_out_world() -> None:
    fixture = load_vertical_slice()
    provisional = generate_fixture_benchmark(fixture, 20260811)
    worlds = [world.model_copy(update={"split": "test"}) for world in provisional.worlds]
    benchmark = provisional.model_copy(
        update={
            "scope_status": "RELEASE_DATASET_A",
            "acceptance_eligible": True,
            "blocking_reasons": [],
            "worlds": worlds,
        }
    )
    benchmark_bytes = canonical_json_bytes(benchmark.model_dump(mode="json"))
    corpus = build_release_corpus(
        [fixture.compiled_trial],
        [fixture.raw_trial],
        [fixture.review],
    )
    evidence = CuratedRetrievalEvidence(
        status="CURATED_ADJUDICATED",
        benchmark_sha256=hashlib.sha256(benchmark_bytes).hexdigest(),
        corpus_trial_hashes={fixture.compiled_trial.nct_id: fixture.compiled_trial.content_hash},
        retrieval_config_sha256="a" * 64,
        query_artifact_sha256="b" * 64,
        snapshot_manifest_sha256="c" * 64,
        run_id="retrieval-test-run",
        git_sha="d" * 40,
        queries=[_query(world.world_id) for world in worlds],
    )

    result = evaluate_curated_retrieval(
        evidence,
        benchmark=benchmark,
        benchmark_bytes=benchmark_bytes,
        corpus=corpus,
    )

    assert result["acceptance_eligible"] is True
    assert result["query_count"] == len(worlds)
    assert result["baselines"]["full_three_source_rrf"]["recall_at_20"] == 1.0
    assert result["exact_condition_irrelevance_exclusion_count"] == 0


def test_curated_retrieval_rejects_exact_match_excluded_as_irrelevant() -> None:
    payload = _query("world-1").model_dump(mode="json")
    payload["irrelevance_decisions"] = {"NCT05239624": True}
    with pytest.raises(ValueError, match="RETRIEVAL_IRRELEVANCE_GATE_MISMATCH"):
        CuratedRetrievalQuery.model_validate(payload)
