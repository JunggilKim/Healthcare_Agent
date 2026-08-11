from __future__ import annotations

from fastapi import APIRouter

from backend.app.main_constants import DISCLAIMER
from backend.app.settings import get_settings

router = APIRouter(tags=["config"])


@router.get("/config/public")
async def public_config() -> dict[str, object]:
    settings = get_settings()
    return {
        "supported_modes": ["snapshot", "live"],
        "default_mode": "snapshot",
        "live_available": (settings.allow_live_model_calls and settings.allow_live_ctgov_calls),
        "snapshot_data_date": "2026-08-11",
        "snapshot_version": "phase1-s004-v1",
        "disclaimer": DISCLAIMER,
        "limits": {"max_patient_chars": 12000, "max_answer_chars": 4000, "max_questions": 5},
        "model_labels": {
            "primary": "Gemini 3.6 Flash (Live Mode only)",
            "lite": "Gemini 3.5 Flash-Lite (Live Mode only)",
        },
    }
