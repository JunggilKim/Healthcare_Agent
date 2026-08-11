from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from backend.app.infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError
from backend.app.infrastructure.local_artifacts import LocalArtifactStore
from backend.app.retrieval.ctgov_client import (
    CTGOV_USER_AGENT,
    STATUS_FILTER,
    ClinicalTrialsGovClient,
    CtgovUnavailableError,
)


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_ctgov_contract_uses_comma_filter_cache_and_fixed_headers(tmp_path: Path) -> None:
    fixture_root = Path("data/fixtures/retrieval/S004")
    search_content = (fixture_root / "search_response.json").read_bytes()
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/version"):
            return httpx.Response(
                200,
                json={"apiVersion": "2.0.5", "dataTimestamp": "2026-08-11T09:00:06"},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200, content=search_content, headers={"content-type": "application/json"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=transport,
        headers={"User-Agent": CTGOV_USER_AGENT, "Accept": "application/json"},
    ) as http_client:
        client = ClinicalTrialsGovClient(LocalArtifactStore(tmp_path), client=http_client)
        first = await client.search("bladder cancer")
        second = await client.search("bladder cancer")

    assert first.cache_sha256 == second.cache_sha256
    assert len(seen) == 2
    assert seen[1].headers["user-agent"] == CTGOV_USER_AGENT
    assert seen[1].url.params["filter.overallStatus"] == STATUS_FILTER
    assert "|" not in seen[1].url.params["filter.overallStatus"]
    assert seen[1].url.params["pageSize"] == "100"
    assert seen[1].url.params["countTotal"] == "true"


@pytest.mark.asyncio
async def test_complete_study_response_is_validated_and_cached_by_exact_bytes(
    tmp_path: Path,
) -> None:
    raw = Path("data/fixtures/retrieval/S004/NCT05239624.raw.json").read_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(
                200,
                json={"apiVersion": "2.0.5", "dataTimestamp": "2026-08-11T09:00:06"},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        response = await ClinicalTrialsGovClient(
            LocalArtifactStore(tmp_path), client=http_client
        ).study("NCT05239624")
    assert response.content == raw
    assert response.cache_path.read_bytes() == raw
    assert (
        response.cache_sha256 == "6e342382edc485fd43d567e5b5029b7f4bff550354698199af4320bb6d034532"
    )


@pytest.mark.asyncio
async def test_retry_policy_and_circuit_open_after_five_exhausted_calls(tmp_path: Path) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request, headers={"content-type": "application/json"})

    clock_value = 0.0
    circuit = CircuitBreaker(clock=lambda: clock_value)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2", transport=transport
    ) as http_client:
        client = ClinicalTrialsGovClient(
            LocalArtifactStore(tmp_path),
            client=http_client,
            circuit=circuit,
            sleep=_no_sleep,
            jitter=lambda _low, _high: 0.0,
        )
        for _ in range(5):
            with pytest.raises(CtgovUnavailableError):
                await client.version()
        assert attempts == 15
        with pytest.raises(CircuitOpenError):
            await client.version()
        clock_value = 61.0
        with pytest.raises(CtgovUnavailableError):
            await client.version()


@pytest.mark.asyncio
async def test_non_json_response_is_rejected_without_retry(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="not json", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = ClinicalTrialsGovClient(LocalArtifactStore(tmp_path), client=http_client)
        with pytest.raises(CtgovUnavailableError):
            await client.version()
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "429", "500"])
async def test_timeout_429_and_500_each_exhaust_bounded_retries(
    tmp_path: Path, failure: str
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("recorded timeout", request=request)
        return httpx.Response(
            int(failure), request=request, headers={"content-type": "application/json"}
        )

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = ClinicalTrialsGovClient(
            LocalArtifactStore(tmp_path),
            client=http_client,
            sleep=_no_sleep,
            jitter=lambda _low, _high: 0.0,
        )
        with pytest.raises(CtgovUnavailableError):
            await client.version()
    assert calls == 3


@pytest.mark.asyncio
async def test_schema_invalid_study_page_is_rejected_and_not_cached(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(
                200,
                json={"apiVersion": "2.0.5", "dataTimestamp": "2026-08-11T09:00:06"},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            json={"studies": [{"unexpected": "shape"}]},
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(
        base_url="https://clinicaltrials.gov/api/v2",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = ClinicalTrialsGovClient(LocalArtifactStore(tmp_path), client=http_client)
        with pytest.raises(CtgovUnavailableError, match=r"invalid.*studies"):
            await client.search("bladder cancer")
    assert not list(tmp_path.glob("ctgov/search/*.json"))
