from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import orjson
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.infrastructure.circuit_breaker import CircuitBreaker
from backend.app.infrastructure.local_artifacts import ArtifactCorruptionError, LocalArtifactStore
from backend.app.retrieval.ctgov_parser import (
    CtgovSchemaError,
    validate_single_study,
    validate_study_page,
)

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
CTGOV_USER_AGENT = "TRIAL-OPT/1.0 (academic competition prototype; ClinicalTrials.gov API v2)"
STATUS_FILTER = "RECRUITING,NOT_YET_RECRUITING,ENROLLING_BY_INVITATION"
SEARCH_FIELDS = ",".join(
    [
        "NCTId",
        "BriefTitle",
        "OfficialTitle",
        "OverallStatus",
        "StartDate",
        "CompletionDate",
        "LastUpdatePostDate",
        "LeadSponsorName",
        "CollaboratorName",
        "Condition",
        "Keyword",
        "StudyType",
        "Phase",
        "InterventionName",
        "InterventionType",
        "BriefSummary",
        "DetailedDescription",
        "EligibilityCriteria",
        "Sex",
        "MinimumAge",
        "MaximumAge",
        "HealthyVolunteers",
        "LocationFacility",
        "LocationCity",
        "LocationState",
        "LocationCountry",
        "VersionHolder",
    ]
)
RETRY_STATUS_CODES = frozenset({429, 500, 501, 502, 503, 504})


class CtgovUnavailableError(RuntimeError):
    pass


class _VersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apiVersion: str
    dataTimestamp: str


@dataclass(frozen=True)
class CtgovResponse:
    content: bytes
    api_version: str
    data_timestamp: str
    retrieved_at: datetime
    cache_sha256: str
    cache_path: Path


class ClinicalTrialsGovClient:
    def __init__(
        self,
        cache: LocalArtifactStore,
        *,
        client: httpx.AsyncClient | None = None,
        circuit: CircuitBreaker | None = None,
        sleep: Any = asyncio.sleep,
        jitter: Any = random.uniform,
    ) -> None:
        self.cache = cache
        self._client = client
        self.circuit = circuit or CircuitBreaker(name="ctgov")
        self._sleep = sleep
        self._jitter = jitter
        self._api_version: str | None = None
        self._data_timestamp: str | None = None
        self._version_lock = asyncio.Lock()

    async def _request(self, path: str, params: dict[str, str | int | bool] | None = None) -> bytes:
        self.circuit.before_call()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                if self._client is None:
                    async with httpx.AsyncClient(
                        base_url=CTGOV_BASE_URL,
                        headers={"User-Agent": CTGOV_USER_AGENT, "Accept": "application/json"},
                        timeout=httpx.Timeout(10.0, connect=3.0),
                    ) as client:
                        response = await client.get(path, params=params)
                else:
                    response = await self._client.get(path, params=params)
                if response.status_code in RETRY_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"retryable ClinicalTrials.gov status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    raise CtgovUnavailableError(
                        f"unexpected ClinicalTrials.gov content type: {content_type or 'missing'}"
                    )
                self.circuit.record_success()
                return response.content
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as error:
                last_error = error
                if isinstance(error, httpx.HTTPStatusError) and (
                    error.response.status_code not in RETRY_STATUS_CODES
                ):
                    break
                if attempt < 2:
                    delay = (0.5, 1.5)[attempt] + float(self._jitter(0.0, 0.25))
                    await self._sleep(delay)
            except CtgovUnavailableError:
                self.circuit.record_failure()
                raise
        self.circuit.record_failure()
        raise CtgovUnavailableError(
            "ClinicalTrials.gov request failed after 3 attempts"
        ) from last_error

    async def version(self) -> tuple[str, str]:
        if self._api_version is None or self._data_timestamp is None:
            async with self._version_lock:
                if self._api_version is None or self._data_timestamp is None:
                    raw = await self._request("/version")
                    try:
                        parsed = _VersionResponse.model_validate(orjson.loads(raw))
                    except (orjson.JSONDecodeError, ValidationError) as error:
                        self.circuit.record_failure()
                        raise CtgovUnavailableError(
                            "invalid ClinicalTrials.gov version response"
                        ) from error
                    self._api_version = parsed.apiVersion
                    self._data_timestamp = parsed.dataTimestamp
        return self._api_version, self._data_timestamp

    async def search(self, condition: str, *, page_size: int = 100) -> CtgovResponse:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        cache_material = f"{condition}\0{page_size}\0{STATUS_FILTER}".encode()
        cache_key = hashlib.sha256(cache_material).hexdigest()
        reference = await asyncio.to_thread(
            self.cache.read_reference, "ctgov/search-index", cache_key
        )
        if reference is not None:
            try:
                cache_path = Path(str(reference["cache_path"]))
                digest = str(reference["sha256"])
                content = await asyncio.to_thread(self.cache.read_verified, cache_path, digest)
                await asyncio.to_thread(validate_study_page, content)
                cached_response = CtgovResponse(
                    content=content,
                    api_version=str(reference["api_version"]),
                    data_timestamp=str(reference["data_timestamp"]),
                    retrieved_at=datetime.fromisoformat(str(reference["retrieved_at"])),
                    cache_sha256=digest,
                    cache_path=cache_path,
                )
            except (
                ArtifactCorruptionError,
                CtgovSchemaError,
                KeyError,
                OSError,
                TypeError,
                ValueError,
            ):
                pass
            else:
                return cached_response
        api_version, data_timestamp = await self.version()
        content = await self._request(
            "/studies",
            {
                "query.cond": condition,
                "filter.overallStatus": STATUS_FILTER,
                "pageSize": page_size,
                "countTotal": "true",
                "fields": SEARCH_FIELDS,
            },
        )
        try:
            await asyncio.to_thread(validate_study_page, content)
        except CtgovSchemaError as error:
            self.circuit.record_failure()
            raise CtgovUnavailableError("invalid ClinicalTrials.gov studies response") from error
        digest, path = await asyncio.to_thread(self.cache.put, "ctgov/search", content)
        response = CtgovResponse(
            content=content,
            api_version=api_version,
            data_timestamp=data_timestamp,
            retrieved_at=datetime.now(UTC),
            cache_sha256=digest,
            cache_path=path,
        )
        await asyncio.to_thread(
            self.cache.put_reference,
            "ctgov/search-index",
            cache_key,
            {
                "sha256": digest,
                "cache_path": str(path),
                "api_version": api_version,
                "data_timestamp": data_timestamp,
                "retrieved_at": response.retrieved_at.isoformat(),
            },
        )
        return response

    async def study(self, nct_id: str) -> CtgovResponse:
        api_version, data_timestamp = await self.version()
        content = await self._request(f"/studies/{quote(nct_id, safe='')}")
        try:
            await asyncio.to_thread(validate_single_study, content)
        except CtgovSchemaError as error:
            self.circuit.record_failure()
            raise CtgovUnavailableError("invalid ClinicalTrials.gov study response") from error
        digest, path = await asyncio.to_thread(self.cache.put, f"ctgov/raw/{nct_id}", content)
        return CtgovResponse(
            content=content,
            api_version=api_version,
            data_timestamp=data_timestamp,
            retrieved_at=datetime.now(UTC),
            cache_sha256=digest,
            cache_path=path,
        )
