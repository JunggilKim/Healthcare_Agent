from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from backend.app.settings import REPOSITORY_ROOT

DemoSupportLevel = Literal["full_evaluation", "retrieval_only"]


@lru_cache(maxsize=1)
def load_demo_cases() -> tuple[dict[str, Any], ...]:
    payload = json.loads(
        (REPOSITORY_ROOT / "data/seeds/synthetic-patients.json").read_text(
            encoding="utf-8"
        )
    )
    return tuple(dict(item) for item in payload["topics"])


def demo_case(case_id: str) -> dict[str, Any]:
    for item in load_demo_cases():
        if item["num"] == case_id:
            return item
    raise ValueError(f"seed case not found: {case_id}")


def demo_support_level(case_id: str | None) -> DemoSupportLevel:
    if not case_id:
        return "full_evaluation"
    value = demo_case(case_id).get("support_level", "retrieval_only")
    if value not in {"full_evaluation", "retrieval_only"}:
        raise ValueError(f"invalid demo support level: {case_id}:{value}")
    return "full_evaluation" if value == "full_evaluation" else "retrieval_only"


def demo_retrieval_concept(case_id: str) -> str:
    value = str(demo_case(case_id).get("retrieval_concept", "")).strip()
    if not value:
        raise ValueError(f"seed retrieval concept missing: {case_id}")
    return value
