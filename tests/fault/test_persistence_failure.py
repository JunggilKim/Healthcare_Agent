from __future__ import annotations

from datetime import UTC, datetime

import orjson
import pytest
from google.api_core.exceptions import ServiceUnavailable

from backend.app.domain.sessions import SessionState
from backend.app.infrastructure import resilient_gcp_store
from backend.app.infrastructure.resilient_gcp_store import (
    PersistenceResilientStore,
    PersistenceUnavailableError,
)


class _FailingGcpStore:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.firestore = object()
        self.fail_create = fail_create
        self.payloads: dict[str, dict[str, object]] = {}

    async def initialize(self) -> None:
        return None

    def token_hash(self, token: str) -> str:
        return f"hash:{token}"

    async def create_session(self, session_id: str, token: str, payload: dict[str, object]) -> None:
        del token
        if self.fail_create:
            raise ServiceUnavailable("create unavailable")
        now = datetime.now(UTC).isoformat()
        self.payloads[session_id] = {
            **payload,
            "state": SessionState.CREATED.value,
            "patient_state_version": 0,
            "created_at": now,
            "updated_at": now,
        }

    async def read_session(self, session_id: str) -> dict[str, object] | None:
        return self.payloads.get(session_id)

    async def transition_and_append(self, **kwargs):
        del kwargs
        raise ServiceUnavailable("recorded Firestore write failure")

    async def write_json_artifact(self, namespace: str, payload: object) -> tuple[str, str]:
        del namespace, payload
        raise AssertionError("volatile export must not reach GCS")


@pytest.mark.asyncio
async def test_post_create_write_failure_finishes_in_volatile_mode_and_disables_export(
    monkeypatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(resilient_gcp_store.logger, "error", messages.append)
    delegate = _FailingGcpStore()
    store = PersistenceResilientStore(delegate)  # type: ignore[arg-type]
    payload = {
        "session_id": "session-sensitive-id",
        "mode": "live",
        "degradation_codes": [],
        "expires_at": "2026-08-19T00:00:00+00:00",
    }
    await store.create_session("session-sensitive-id", "secret-token", payload)

    first = await store.transition_and_append(
        session_id="session-sensitive-id",
        expected_state=SessionState.CREATED,
        target_state=SessionState.INPUT_VALIDATING,
        event_type="INPUT_VALIDATED",
        payload={},
        session_payload=payload,
        patient_state_version=0,
    )
    second = await store.transition_and_append(
        session_id="session-sensitive-id",
        expected_state=SessionState.INPUT_VALIDATING,
        target_state=SessionState.PATIENT_EXTRACTING,
        event_type="PATIENT_EXTRACTION_STARTED",
        payload={},
        session_payload=payload,
        patient_state_version=0,
    )

    session = await store.read_session("session-sensitive-id")
    assert session is not None
    assert first.sequence == 1
    assert second.sequence == 2
    assert session["state"] == SessionState.PATIENT_EXTRACTING.value
    assert session["export_available"] is False
    assert session["durable_replay"] is False
    assert session["persistence_status"] == "VOLATILE_RESULT_ONLY"
    assert "PERSISTENCE_FAILURE_VOLATILE_RESULT" in session["degradation_codes"]
    assert await store.authenticate("session-sensitive-id", "secret-token")
    assert not await store.authenticate("session-sensitive-id", "wrong-token")
    with pytest.raises(PersistenceUnavailableError):
        await store.write_json_artifact("sessions/session-sensitive-id/exports", {})
    with pytest.raises(PersistenceUnavailableError):
        await store.delete_session("session-sensitive-id")

    assert len(messages) == 1
    structured = orjson.loads(messages[0])
    assert structured["severity"] == "ERROR"
    assert structured["degradation_code"] == "PERSISTENCE_FAILURE_VOLATILE_RESULT"
    assert "session-sensitive-id" not in messages[0]
    assert "secret-token" not in messages[0]


@pytest.mark.asyncio
async def test_session_creation_failure_is_not_converted_to_volatile_success() -> None:
    store = PersistenceResilientStore(  # type: ignore[arg-type]
        _FailingGcpStore(fail_create=True)
    )
    with pytest.raises(ServiceUnavailable):
        await store.create_session(
            "session-not-created",
            "token",
            {
                "mode": "live",
                "degradation_codes": [],
                "expires_at": "2026-08-19T00:00:00+00:00",
            },
        )
