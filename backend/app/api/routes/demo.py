from __future__ import annotations

import json

from fastapi import APIRouter

from backend.app.settings import REPOSITORY_ROOT

router = APIRouter(tags=["demo"])


@router.get("/demo/cases")
async def demo_cases() -> dict[str, object]:
    path = REPOSITORY_ROOT / "data" / "seeds" / "synthetic-patients.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "cases": [
            {
                "id": item["num"],
                "text": item["title"],
                "has_full_snapshot": item["num"] == "S004",
            }
            for item in payload["topics"]
        ]
    }
