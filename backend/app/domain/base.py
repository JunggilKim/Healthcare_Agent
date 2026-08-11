from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base contract for logic-bearing external and persisted schemas."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
