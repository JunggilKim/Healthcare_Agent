from __future__ import annotations

from datetime import datetime

from backend.app.domain.base import StrictModel
from backend.app.domain.values import JsonValue


class SessionEvent(StrictModel):
    event_id: str
    session_id: str
    sequence: int
    event_type: str
    payload: dict[str, JsonValue]
    created_at: datetime
