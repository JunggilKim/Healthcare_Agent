from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import orjson
from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel
from backend.app.evaluation.corpus import ReleaseCorpus
from backend.app.evaluation.metrics import mean, retrieval_metrics
from backend.app.evaluation.models import BenchmarkArtifact

RETRIEVAL_BASELINES = {
    "ctgov_rank_only",
    "bm25_only",
    "embedding_only",
    "ctgov_bm25_rrf",
    "full_three_source_rrf",
}


class CuratedRetrievalQuery(StrictModel):
    query_id: str
    world_id: str
    target_nct_id: str
    split: Literal["test"] = "test"
    qrels: dict[str, int]
    baseline_orders: dict[str, list[str]]
    full_rrf_scores: dict[str, float]
    exact_condition_matches: dict[str, bool]
    compiled_condition_slot_matches: dict[str, bool]
    irrelevance_decisions: dict[str, bool]
    detailed_nct_ids: list[str] = Field(min_length=1, max_length=8)
    reviewer_labels: list[str] = Field(min_length=1)
    adjudicated_at: datetime

    @model_validator(mode="after")
    def validate_query_evidence(self) -> CuratedRetrievalQuery:
        if set(self.baseline_orders) != RETRIEVAL_BASELINES:
            raise ValueError("RETRIEVAL_BASELINE_SET_INVALID")
        full = self.baseline_orders["full_three_source_rrf"]
        if not full or len(full) > 20 or len(set(full)) != len(full):
            raise ValueError("RETRIEVAL_FULL_ORDER_INVALID")
        candidate_set = set(full)
        for baseline, order in self.baseline_orders.items():
            if len(order) != len(set(order)) or set(order) != candidate_set:
                raise ValueError(f"RETRIEVAL_BASELINE_CANDIDATE_SET_MISMATCH:{baseline}")
        if self.detailed_nct_ids != full[:8]:
            raise ValueError("RETRIEVAL_DETAILED_SET_NOT_FULL_RRF_TOP8")
        if not self.qrels or not any(value > 0 for value in self.qrels.values()):
            raise ValueError("RETRIEVAL_QRELS_NO_RELEVANT_TRIAL")
        if any(value not in {0, 1, 2} for value in self.qrels.values()):
            raise ValueError("RETRIEVAL_QREL_GRADE_INVALID")
        for name, values in (
            ("scores", self.full_rrf_scores),
            ("exact", self.exact_condition_matches),
            ("compiled", self.compiled_condition_slot_matches),
            ("irrelevance", self.irrelevance_decisions),
        ):
            if set(values) != candidate_set:
                raise ValueError(f"RETRIEVAL_{name.upper()}_CANDIDATE_SET_MISMATCH")
        if any(score < 0 or score > 1 for score in self.full_rrf_scores.values()):
            raise ValueError("RETRIEVAL_SCORE_OUT_OF_RANGE")
        for nct_id in candidate_set:
            expected_irrelevant = (
                self.full_rrf_scores[nct_id] < 0.15
                and not self.exact_condition_matches[nct_id]
                and not self.compiled_condition_slot_matches[nct_id]
            )
            if self.irrelevance_decisions[nct_id] != expected_irrelevant:
                raise ValueError(f"RETRIEVAL_IRRELEVANCE_GATE_MISMATCH:{nct_id}")
        if len(set(self.reviewer_labels)) != len(self.reviewer_labels):
            raise ValueError("RETRIEVAL_REVIEWER_LABEL_DUPLICATE")
        return self


class CuratedRetrievalEvidence(StrictModel):
    schema_version: Literal["trial-opt-curated-retrieval-v1"] = "trial-opt-curated-retrieval-v1"
    status: Literal["CURATED_ADJUDICATED"]
    benchmark_sha256: str
    corpus_trial_hashes: dict[str, str]
    retrieval_config_sha256: str
    query_artifact_sha256: str
    snapshot_manifest_sha256: str
    run_id: str
    git_sha: str
    queries: list[CuratedRetrievalQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_queries(self) -> CuratedRetrievalEvidence:
        query_ids = [item.query_id for item in self.queries]
        world_ids = [item.world_id for item in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("RETRIEVAL_QUERY_ID_DUPLICATE")
        if len(set(world_ids)) != len(world_ids):
            raise ValueError("RETRIEVAL_WORLD_ID_DUPLICATE")
        for value in (
            self.benchmark_sha256,
            self.retrieval_config_sha256,
            self.query_artifact_sha256,
            self.snapshot_manifest_sha256,
        ):
            if len(value) != 64:
                raise ValueError("RETRIEVAL_PROVENANCE_HASH_INVALID")
        return self


def load_curated_retrieval_evidence(path: str) -> CuratedRetrievalEvidence:
    return CuratedRetrievalEvidence.model_validate(orjson.loads(Path(path).read_bytes()))


def validate_curated_retrieval_evidence(
    evidence: CuratedRetrievalEvidence,
    *,
    benchmark: BenchmarkArtifact,
    benchmark_bytes: bytes,
    corpus: ReleaseCorpus,
) -> None:
    if not benchmark.acceptance_eligible or benchmark.scope_status != "RELEASE_DATASET_A":
        raise ValueError("RETRIEVAL_RELEASE_BENCHMARK_REQUIRED")
    if hashlib.sha256(benchmark_bytes).hexdigest() != evidence.benchmark_sha256:
        raise ValueError("RETRIEVAL_BENCHMARK_HASH_MISMATCH")
    expected_hashes = {
        nct_id: trial.content_hash for nct_id, trial in corpus.compiled_trials.items()
    }
    if evidence.corpus_trial_hashes != expected_hashes:
        raise ValueError("RETRIEVAL_CORPUS_HASH_MISMATCH")
    test_worlds = {item.world_id: item for item in benchmark.worlds if item.split == "test"}
    queries = {item.world_id: item for item in evidence.queries}
    if set(queries) != set(test_worlds):
        raise ValueError("RETRIEVAL_TEST_WORLD_COVERAGE_INCOMPLETE")
    corpus_ids = set(corpus.compiled_trials)
    for world_id, query in queries.items():
        world = test_worlds[world_id]
        if query.target_nct_id != world.nct_id:
            raise ValueError(f"RETRIEVAL_QUERY_TARGET_MISMATCH:{world_id}")
        if not set(query.baseline_orders["full_three_source_rrf"]).issubset(corpus_ids):
            raise ValueError(f"RETRIEVAL_QUERY_TRIAL_OUTSIDE_CORPUS:{world_id}")
        if not set(query.qrels).issubset(corpus_ids):
            raise ValueError(f"RETRIEVAL_QREL_TRIAL_OUTSIDE_CORPUS:{world_id}")


def evaluate_curated_retrieval(
    evidence: CuratedRetrievalEvidence,
    *,
    benchmark: BenchmarkArtifact,
    benchmark_bytes: bytes,
    corpus: ReleaseCorpus,
) -> dict[str, Any]:
    validate_curated_retrieval_evidence(
        evidence,
        benchmark=benchmark,
        benchmark_bytes=benchmark_bytes,
        corpus=corpus,
    )
    per_baseline: dict[str, list[dict[str, Any]]] = {
        name: [] for name in sorted(RETRIEVAL_BASELINES)
    }
    predictions: list[dict[str, Any]] = []
    exact_condition_exclusions = 0
    for query in evidence.queries:
        for baseline, order in query.baseline_orders.items():
            metrics = retrieval_metrics(order, query.qrels)
            per_baseline[baseline].append(metrics)
            predictions.extend(
                {
                    "suite": "retrieval",
                    "query_id": query.query_id,
                    "world_id": query.world_id,
                    "baseline": baseline,
                    "rank": rank,
                    "nct_id": nct_id,
                    "qrel": query.qrels.get(nct_id, 0),
                }
                for rank, nct_id in enumerate(order, start=1)
            )
        exact_condition_exclusions += sum(
            query.exact_condition_matches[nct_id] and query.irrelevance_decisions[nct_id]
            for nct_id in query.exact_condition_matches
        )
    aggregated = {
        baseline: {
            metric: mean([float(row[metric]) for row in rows])
            for metric in (
                "recall_at_20",
                "precision_at_5",
                "precision_at_10",
                "ndcg_at_10",
                "mrr",
            )
        }
        for baseline, rows in per_baseline.items()
    }
    return {
        "scope": "DATASET_A_CURATED_RETRIEVAL_TEST_SPLIT",
        "acceptance_eligible": True,
        "query_count": len(evidence.queries),
        "qrels_source": "protocol-text adjudication by project reviewers",
        "baselines": aggregated,
        "exact_condition_irrelevance_exclusion_count": exact_condition_exclusions,
        "provenance": {
            "run_id": evidence.run_id,
            "git_sha": evidence.git_sha,
            "retrieval_config_sha256": evidence.retrieval_config_sha256,
            "query_artifact_sha256": evidence.query_artifact_sha256,
            "snapshot_manifest_sha256": evidence.snapshot_manifest_sha256,
        },
        "predictions": predictions,
    }
