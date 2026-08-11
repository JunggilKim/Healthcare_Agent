from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.app.domain.base import StrictModel


class ModelUsage(StrictModel):
    model_id: str
    task_name: str
    prompt_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    cache_hit: bool
    created_at: datetime


class StructuredGenerationRecord(StrictModel):
    cache_key: str
    model_id: str
    task_name: str
    prompt_version: str
    output_schema_version: str
    parsed_json: dict[str, object]
    usage: ModelUsage
    used_fallback: bool = False
