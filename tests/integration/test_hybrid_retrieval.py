from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import orjson
import pytest
from fastapi.testclient import TestClient

from backend.app.infrastructure.local_artifacts import (
    ArtifactCorruptionError,
    LocalArtifactStore,
)
from backend.app.main import app
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient
from backend.app.retrieval.embeddings import (
    DisabledEmbeddingProvider,
    RecordedEmbeddingProvider,
)
from backend.app.retrieval.models import RetrievalQuery
from backend.app.retrieval.retriever import HybridRetriever
from backend.app.retrieval.snapshot import load_retrieval_snapshot

FIXTURE_ROOT = Path("data/fixtures/retrieval/S004")


def _query() -> RetrievalQuery:
    payload = orjson.loads((FIXTURE_ROOT / "manifest.full.json").read_bytes())
    return RetrievalQuery.model_validate(payload["retrieval_query"])


def _retriever(
    tmp_path: Path,
    *,
    embeddings: RecordedEmbeddingProvider | DisabledEmbeddingProvider,
    client: httpx.AsyncClient | None = None,
) -> HybridRetriever:
    return HybridRetriever(
        ctgov=ClinicalTrialsGovClient(
            LocalArtifactStore(tmp_path / "cache"),
            client=client,
            sleep=lambda _delay: asyncio.sleep(0),
            jitter=lambda _low, _high: 0.0,
        ),
        embeddings=embeddings,
        snapshot_root=FIXTURE_ROOT,
    )


@pytest.mark.asyncio
async def test_snapshot_dense_retrieval_has_fixed_caps_and_allowed_population(
    tmp_path: Path,
) -> None:
    result = await _retriever(
        tmp_path,
        embeddings=RecordedEmbeddingProvider(FIXTURE_ROOT / "embeddings.json"),
    ).retrieve(_query(), mode="snapshot")

    assert result.mode == "snapshot"
    assert result.dense_source_used is True
    assert len(result.ranked_candidates) == 20
    assert len(result.selected_for_compilation) == 8
    assert len({item.nct_id for item in result.ranked_candidates}) == 20
    assert all(item.trial.study_type == "INTERVENTIONAL" for item in result.ranked_candidates)
    assert all(
        item.trial.overall_status in {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}
        for item in result.ranked_candidates
    )
    assert all(item.compiled is False for item in result.ranked_candidates)


@pytest.mark.asyncio
async def test_embedding_failure_discards_entire_dense_source(tmp_path: Path) -> None:
    result = await _retriever(tmp_path, embeddings=DisabledEmbeddingProvider()).retrieve(
        _query(), mode="snapshot"
    )

    assert result.dense_source_used is False
    assert result.mode == "snapshot"
    assert result.degradation_codes == ["EMBEDDING_UNAVAILABLE_LEXICAL_FALLBACK"]
    assert all(
        item.embedding_rank is None and item.full_rrf is None for item in result.ranked_candidates
    )
    assert len(result.ranked_candidates) == 20


@pytest.mark.asyncio
async def test_ctgov_blocked_uses_hash_verified_snapshot_within_bound(tmp_path: Path) -> None:
    async def blocked(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network blocked by test", request=request)

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2", transport=httpx.MockTransport(blocked)
    ) as http_client:
        result = await _retriever(
            tmp_path,
            embeddings=RecordedEmbeddingProvider(FIXTURE_ROOT / "embeddings.json"),
            client=http_client,
        ).retrieve(_query(), mode="live")

    assert result.mode == "hybrid_degraded"
    assert "CTGOV_UNAVAILABLE_SNAPSHOT_USED" in result.degradation_codes
    assert len(result.ranked_candidates) == 20


@pytest.mark.asyncio
async def test_live_retrieval_is_identical_from_exact_raw_cache_when_network_then_blocks(
    tmp_path: Path,
) -> None:
    search = (FIXTURE_ROOT / "search_response.json").read_bytes()

    async def live_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(
                200,
                json={"apiVersion": "2.0.5", "dataTimestamp": "2026-08-11T09:00:06"},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(200, content=search, headers={"content-type": "application/json"})

    cache = LocalArtifactStore(tmp_path / "cache")
    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=httpx.MockTransport(live_handler),
    ) as live_client:
        first = await HybridRetriever(
            ctgov=ClinicalTrialsGovClient(cache, client=live_client),
            embeddings=RecordedEmbeddingProvider(FIXTURE_ROOT / "embeddings.json"),
            snapshot_root=FIXTURE_ROOT,
        ).retrieve(_query(), mode="live")

    async def blocked(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("must not be reached when cache is valid", request=request)

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=httpx.MockTransport(blocked),
    ) as blocked_client:
        second = await HybridRetriever(
            ctgov=ClinicalTrialsGovClient(cache, client=blocked_client),
            embeddings=RecordedEmbeddingProvider(FIXTURE_ROOT / "embeddings.json"),
            snapshot_root=FIXTURE_ROOT,
        ).retrieve(_query(), mode="live")

    assert first.mode == second.mode == "live"
    assert first.registry_data_timestamp == second.registry_data_timestamp
    assert [item.model_dump() for item in first.ranked_candidates] == [
        item.model_dump() for item in second.ranked_candidates
    ]


def test_snapshot_corruption_is_rejected(tmp_path: Path) -> None:
    manifest = (FIXTURE_ROOT / "manifest.json").read_bytes()
    search = (FIXTURE_ROOT / "search_response.json").read_bytes()
    (tmp_path / "manifest.json").write_bytes(manifest)
    (tmp_path / "search_response.json").write_bytes(search + b"corrupt")
    with pytest.raises(ArtifactCorruptionError, match="snapshot hash mismatch"):
        load_retrieval_snapshot(tmp_path)


def test_complete_raw_record_and_source_version_deviation_are_hash_bound() -> None:
    full_manifest = orjson.loads((FIXTURE_ROOT / "manifest.full.json").read_bytes())
    check = orjson.loads((FIXTURE_ROOT / "source_version_check.json").read_bytes())
    raw = (FIXTURE_ROOT / full_manifest["complete_record"]["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == full_manifest["complete_record"]["sha256"]
    assert check["raw_record_sha256"] == full_manifest["complete_record"]["sha256"]

    study = orjson.loads(raw)
    eligibility = study["protocolSection"]["eligibilityModule"]["eligibilityCriteria"]
    assert hashlib.sha256(eligibility.encode()).hexdigest() == check["eligibility_text_sha256"]
    criteria = orjson.loads(
        Path("data/fixtures/vertical_slice/NCT05239624.criteria.json").read_bytes()
    )
    by_id = {item["criterion_id"]: item for item in criteria["criteria"]}
    for criterion_id in check["exact_fragment_matches"]:
        assert by_id[criterion_id]["source_span"]["quote"] in eligibility
    deviation = check["source_version_deviations"][0]
    assert deviation["fixture_text"] not in eligibility
    assert deviation["live_source_text"] in eligibility


def test_s004_retrieval_api_returns_ui_safe_candidate_caps() -> None:
    response = TestClient(app).get("/api/v1/demo/retrieval/S004")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "snapshot"
    assert len(payload["ranked_candidates"]) == 20
    assert len(payload["selected_for_compilation"]) == 8
    assert all(candidate["compiled"] is False for candidate in payload["ranked_candidates"])
