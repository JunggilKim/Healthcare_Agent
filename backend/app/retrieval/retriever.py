from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import orjson

from backend.app.retrieval.bm25 import bm25_ranks, build_trial_document
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient, CtgovUnavailableError
from backend.app.retrieval.ctgov_parser import (
    is_interactive_candidate,
    parse_study,
    validate_study_page,
)
from backend.app.retrieval.embeddings import EmbeddingProvider, EmbeddingUnavailableError
from backend.app.retrieval.models import (
    RankedCandidate,
    RegistryCandidate,
    RetrievalQuery,
    RetrievalResult,
)
from backend.app.retrieval.rrf import (
    cosine_ranks,
    exact_condition_match,
    min_max_scores,
    rrf_score,
)
from backend.app.retrieval.snapshot import load_retrieval_snapshot
from backend.app.retrieval.tokenizer import RegexMedicalTokenizer


class HybridRetriever:
    def __init__(
        self,
        *,
        ctgov: ClinicalTrialsGovClient,
        embeddings: EmbeddingProvider,
        snapshot_root: Path,
        snapshot_embeddings: EmbeddingProvider | None = None,
        tokenizer: RegexMedicalTokenizer | None = None,
    ) -> None:
        self.ctgov = ctgov
        self.embeddings = embeddings
        self.snapshot_embeddings = snapshot_embeddings
        self.snapshot_root = snapshot_root
        self.tokenizer = tokenizer or RegexMedicalTokenizer()

    @staticmethod
    def _merge_page(
        merged: dict[str, RegistryCandidate],
        content: bytes,
        *,
        query: str,
        api_version: str,
        retrieved_at: datetime,
    ) -> None:
        for position, study in enumerate(validate_study_page(content), start=1):
            study_bytes = orjson.dumps(study, option=orjson.OPT_SORT_KEYS)
            trial = parse_study(
                study,
                api_version=api_version,
                retrieved_at=retrieved_at,
                raw_bytes=study_bytes,
            )
            if not is_interactive_candidate(trial):
                continue
            existing = merged.get(trial.nct_id)
            if existing is not None:
                if query not in existing.retrieved_by_queries:
                    existing.retrieved_by_queries.append(query)
                    existing.retrieved_by_queries.sort()
                if position < existing.registry_rank:
                    existing.registry_rank = position
                continue
            if len(merged) >= 100:
                continue
            merged[trial.nct_id] = RegistryCandidate(
                trial=trial,
                registry_rank=position,
                retrieved_by_queries=[query],
            )

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        mode: str = "live",
        allow_snapshot_fallback: bool = True,
    ) -> RetrievalResult:
        merged: dict[str, RegistryCandidate] = {}
        degradation_codes: list[str] = []
        api_version = "unknown"
        data_timestamp = "unknown"
        retrieved_at = datetime.now(UTC)
        result_mode = "live"

        if mode == "snapshot":
            result_mode = "snapshot"
            manifest, content = load_retrieval_snapshot(self.snapshot_root)
            api_version = manifest.api_version
            data_timestamp = manifest.data_timestamp
            retrieved_at = datetime.fromisoformat(data_timestamp.replace("Z", "+00:00"))
            self._merge_page(
                merged,
                content,
                query=query.condition_queries[0].text,
                api_version=api_version,
                retrieved_at=retrieved_at,
            )
        else:
            try:
                ordered_queries = sorted(
                    query.condition_queries, key=lambda item: (item.priority, item.text)
                )
                responses = await asyncio.gather(
                    *(self.ctgov.search(item.text, page_size=100) for item in ordered_queries)
                )
                for condition_query, response in zip(ordered_queries, responses, strict=True):
                    api_version = response.api_version
                    data_timestamp = response.data_timestamp
                    retrieved_at = response.retrieved_at
                    self._merge_page(
                        merged,
                        response.content,
                        query=condition_query.text,
                        api_version=api_version,
                        retrieved_at=retrieved_at,
                    )
                if len(merged) < 20 and query.condition_queries:
                    primary_phrase = query.dense_query.split(";", maxsplit=1)[0].strip()
                    already_used = {item.text.casefold() for item in query.condition_queries}
                    if primary_phrase and primary_phrase.casefold() not in already_used:
                        response = await self.ctgov.search(primary_phrase, page_size=100)
                        self._merge_page(
                            merged,
                            response.content,
                            query=primary_phrase,
                            api_version=response.api_version,
                            retrieved_at=response.retrieved_at,
                        )
            except CtgovUnavailableError:
                if not allow_snapshot_fallback:
                    raise
                manifest, content = load_retrieval_snapshot(self.snapshot_root)
                result_mode = "hybrid_degraded"
                degradation_codes.append("CTGOV_UNAVAILABLE_SNAPSHOT_USED")
                api_version = manifest.api_version
                data_timestamp = manifest.data_timestamp
                retrieved_at = datetime.fromisoformat(data_timestamp.replace("Z", "+00:00"))
                merged.clear()
                self._merge_page(
                    merged,
                    content,
                    query=query.condition_queries[0].text,
                    api_version=api_version,
                    retrieved_at=retrieved_at,
                )

        candidates = sorted(
            merged.values(), key=lambda item: (item.registry_rank, item.trial.nct_id)
        )
        query_text = " ".join(item.text for item in query.condition_queries)
        ranks = bm25_ranks([item.trial for item in candidates], query_text, self.tokenizer)
        query_conditions = [item.text for item in query.condition_queries]
        exact = {
            item.trial.nct_id: exact_condition_match(item, query_conditions) for item in candidates
        }
        lexical = {
            item.trial.nct_id: rrf_score(
                item.registry_rank,
                ranks[item.trial.nct_id],
                exact_match=exact[item.trial.nct_id],
            )
            for item in candidates
        }
        stage_a = sorted(
            candidates,
            key=lambda item: (-lexical[item.trial.nct_id], item.trial.nct_id),
        )[:20]

        embedding_ranks: dict[str, int] | None = None
        full_scores: dict[str, float] | None = None
        try:
            embedding_provider = (
                self.snapshot_embeddings
                if result_mode in {"snapshot", "hybrid_degraded"}
                and self.snapshot_embeddings is not None
                else self.embeddings
            )
            query_vector = await embedding_provider.embed_query(query.dense_query)
            documents = [build_trial_document(item.trial) for item in stage_a]
            vectors = await embedding_provider.embed_documents(documents)
            if len(vectors) != len(stage_a):
                raise EmbeddingUnavailableError("incomplete dense document set")
            embedding_ranks = cosine_ranks(
                query_vector,
                {item.trial.nct_id: vector for item, vector in zip(stage_a, vectors, strict=True)},
            )
            full_scores = {
                item.trial.nct_id: rrf_score(
                    item.registry_rank,
                    ranks[item.trial.nct_id],
                    embedding_ranks[item.trial.nct_id],
                    exact_match=exact[item.trial.nct_id],
                )
                for item in stage_a
            }
        except (EmbeddingUnavailableError, ValueError):
            degradation_codes.append("EMBEDDING_UNAVAILABLE_LEXICAL_FALLBACK")
            if result_mode == "live":
                result_mode = "hybrid_degraded"

        active_scores = (
            full_scores
            if full_scores is not None
            else {item.trial.nct_id: lexical[item.trial.nct_id] for item in stage_a}
        )
        normalized = min_max_scores(active_scores)
        ordered = sorted(
            stage_a,
            key=lambda item: (-active_scores[item.trial.nct_id], item.trial.nct_id),
        )
        ranked = [
            RankedCandidate(
                nct_id=item.trial.nct_id,
                registry_rank=item.registry_rank,
                bm25_rank=ranks[item.trial.nct_id],
                embedding_rank=(embedding_ranks or {}).get(item.trial.nct_id),
                exact_condition_match=exact[item.trial.nct_id],
                lexical_rrf=lexical[item.trial.nct_id],
                full_rrf=full_scores[item.trial.nct_id] if full_scores is not None else None,
                retrieval_score=normalized[item.trial.nct_id],
                trial=item.trial,
            )
            for item in ordered
        ]
        return RetrievalResult(
            mode=result_mode,
            api_version=api_version,
            registry_data_timestamp=data_timestamp,
            retrieved_at=retrieved_at,
            dense_source_used=full_scores is not None,
            degradation_codes=degradation_codes,
            ranked_candidates=ranked,
            selected_for_compilation=[item.nct_id for item in ranked[:8]],
        )
