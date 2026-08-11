from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.app.api.errors import ApiProblem
from backend.app.application.vertical_slice import load_vertical_slice

router = APIRouter(tags=["trials"])


@router.get("/trials/{nct_id}")
async def read_public_trial_source(nct_id: str) -> dict[str, Any]:
    """Return only committed source data; this endpoint never dispatches model calls."""

    fixture = load_vertical_slice()
    if nct_id != fixture.raw_trial.nct_id:
        raise ApiProblem(
            404,
            "TRIAL_SOURCE_NOT_FOUND",
            "Trial source not found",
            "The trial is not present in the frozen public source cache.",
        )
    raw = fixture.raw_trial.model_dump(mode="json")
    return {
        "nct_id": nct_id,
        "source": "ClinicalTrials.gov committed snapshot",
        "source_timestamp": raw["retrieved_at"],
        "source_json_sha256": raw["source_json_sha256"],
        "trial": raw,
    }
