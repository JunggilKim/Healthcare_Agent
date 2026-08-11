from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.settings import config_bundle_hash, get_settings

router = APIRouter(tags=["health"])


class HealthChecks(StrictModel):
    local_store: Literal["ok", "unknown", "failed"]
    firestore: Literal["ok", "unknown", "failed"]
    gcs: Literal["ok", "unknown", "failed"]
    gemini_circuit: Literal["closed", "open", "half_open"]
    ctgov_circuit: Literal["closed", "open", "half_open"]


class HealthResponse(StrictModel):
    status: Literal["ok", "degraded"]
    version: str
    snapshot_version: str
    config_hash: str = Field(min_length=64, max_length=64)
    checks: HealthChecks


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return process/config health without performing a network or paid model call."""

    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        snapshot_version=settings.demo_snapshot_version or "phase-0-unbuilt",
        config_hash=config_bundle_hash(),
        checks=HealthChecks(
            local_store="ok",
            firestore="unknown",
            gcs="unknown",
            gemini_circuit="closed",
            ctgov_circuit="closed",
        ),
    )
