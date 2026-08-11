from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import orjson
from google.api_core.exceptions import GoogleAPICallError, RetryError

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.events import SessionEvent
from backend.app.domain.sessions import SessionState
from backend.app.infrastructure.gcp_store import GcpSessionStore

logger = logging.getLogger("trial_opt.persistence")


class PersistenceUnavailableError(RuntimeError):
    pass


class PersistenceResilientStore:
    """Finish an in-flight request in memory after a post-create GCP outage.

    The shadow is process-local and explicitly disables export and durable replay. Creation
    failures and integrity/contract errors are never converted into a volatile session.
    """

    def __init__(self, delegate: GcpSessionStore) -> None:
        self.delegate = delegate
        self.firestore = delegate.firestore
        self._sessions: dict[str, dict[str, Any]] = {}
        self._token_hashes: dict[str, str] = {}
        self._events: dict[str, list[SessionEvent]] = {}
        self._volatile: set[str] = set()
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, list[dict[str, Any]] | None]] = {}

    async def initialize(self) -> None:
        await self.delegate.initialize()

    @staticmethod
    def _copy(value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], orjson.loads(canonical_json_bytes(value)))

    @staticmethod
    def _is_dependency_failure(error: BaseException) -> bool:
        return isinstance(error, (GoogleAPICallError, RetryError, TimeoutError, OSError))

    def _activate_volatile(
        self, session_id: str, error: BaseException, payload: dict[str, Any] | None = None
    ) -> None:
        if session_id not in self._sessions:
            raise error
        if payload is not None:
            shadow = self._sessions[session_id]
            metadata = {
                key: shadow[key]
                for key in ("state", "patient_state_version", "created_at", "updated_at")
            }
            self._sessions[session_id] = {**self._copy(payload), **metadata}
        session = self._sessions[session_id]
        codes = list(session.get("degradation_codes", []))
        if "PERSISTENCE_FAILURE_VOLATILE_RESULT" not in codes:
            codes.append("PERSISTENCE_FAILURE_VOLATILE_RESULT")
        degraded = {
            "degradation_codes": codes,
            "export_available": False,
            "durable_replay": False,
            "persistence_status": "VOLATILE_RESULT_ONLY",
        }
        session.update(degraded)
        if payload is not None:
            payload.update(degraded)
        first_activation = session_id not in self._volatile
        self._volatile.add(session_id)
        if first_activation:
            logger.error(
                canonical_json_bytes(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "severity": "ERROR",
                        "request_id": None,
                        "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest(),
                        "event_type": "persistence_degraded",
                        "stage": "persistence",
                        "mode": session.get("mode"),
                        "model_id": None,
                        "task_name": None,
                        "cache_hit": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "estimated_cost_usd": None,
                        "latency_ms": None,
                        "retry_count": None,
                        "degradation_code": "PERSISTENCE_FAILURE_VOLATILE_RESULT",
                        "error_code": type(error).__name__,
                        "git_sha": "runtime",
                    }
                ).decode()
            )

    async def create_session(self, session_id: str, token: str, payload: dict[str, Any]) -> None:
        await self.delegate.create_session(session_id, token, payload)
        now = datetime.now(UTC).isoformat()
        self._sessions[session_id] = {
            **self._copy(payload),
            "state": SessionState.CREATED.value,
            "patient_state_version": 0,
            "created_at": now,
            "updated_at": now,
        }
        self._token_hashes[session_id] = self.delegate.token_hash(token)
        self._events[session_id] = []

    async def authenticate(self, session_id: str, token: str) -> bool:
        if session_id in self._volatile:
            expected = self._token_hashes.get(session_id)
            return expected is not None and hmac.compare_digest(
                expected, self.delegate.token_hash(token)
            )
        try:
            return await self.delegate.authenticate(session_id, token)
        except Exception as error:
            if not self._is_dependency_failure(error):
                raise
            self._activate_volatile(session_id, error)
            return await self.authenticate(session_id, token)

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        if session_id in self._volatile:
            return self._copy(self._sessions[session_id])
        try:
            payload = await self.delegate.read_session(session_id)
        except Exception as error:
            if not self._is_dependency_failure(error):
                raise
            self._activate_volatile(session_id, error)
            return self._copy(self._sessions[session_id])
        if payload is not None and session_id in self._sessions:
            self._sessions[session_id] = self._copy(payload)
        return payload

    def _volatile_transition(
        self,
        *,
        session_id: str,
        expected_state: SessionState,
        target_state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        session_payload: dict[str, Any],
        patient_state_version: int,
    ) -> SessionEvent:
        current = self._sessions.get(session_id)
        if current is None:
            raise KeyError(session_id)
        if current["state"] != expected_state.value:
            raise ValueError(
                f"session state changed: expected {expected_state.value}, found {current['state']}"
            )
        now = datetime.now(UTC)
        event = SessionEvent(
            event_id=f"evt_{uuid4()}",
            session_id=session_id,
            sequence=len(self._events[session_id]) + 1,
            event_type=event_type,
            payload=payload,
            created_at=now,
        )
        self._events[session_id].append(event)
        self._sessions[session_id] = {
            **self._copy(session_payload),
            "state": target_state.value,
            "patient_state_version": patient_state_version,
            "created_at": current["created_at"],
            "updated_at": now.isoformat(),
        }
        self._activate_volatile(
            session_id,
            PersistenceUnavailableError("volatile persistence continuation"),
            session_payload,
        )
        return event

    async def transition_and_append(
        self,
        *,
        session_id: str,
        expected_state: SessionState,
        target_state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        session_payload: dict[str, Any],
        patient_state_version: int,
    ) -> SessionEvent:
        if session_id in self._volatile:
            return self._volatile_transition(
                session_id=session_id,
                expected_state=expected_state,
                target_state=target_state,
                event_type=event_type,
                payload=payload,
                session_payload=session_payload,
                patient_state_version=patient_state_version,
            )
        try:
            event = await self.delegate.transition_and_append(
                session_id=session_id,
                expected_state=expected_state,
                target_state=target_state,
                event_type=event_type,
                payload=payload,
                session_payload=session_payload,
                patient_state_version=patient_state_version,
            )
        except Exception as error:
            if not self._is_dependency_failure(error):
                raise
            self._activate_volatile(session_id, error, session_payload)
            return self._volatile_transition(
                session_id=session_id,
                expected_state=expected_state,
                target_state=target_state,
                event_type=event_type,
                payload=payload,
                session_payload=session_payload,
                patient_state_version=patient_state_version,
            )
        current = self._sessions[session_id]
        self._sessions[session_id] = {
            **self._copy(session_payload),
            "state": target_state.value,
            "patient_state_version": patient_state_version,
            "created_at": current["created_at"],
            "updated_at": event.created_at.isoformat(),
        }
        self._events[session_id].append(event)
        return event

    async def append_event_without_transition(
        self, *, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> SessionEvent:
        session = await self.read_session(session_id)
        if session is None:
            raise KeyError(session_id)
        state = SessionState(session["state"])
        return await self.transition_and_append(
            session_id=session_id,
            expected_state=state,
            target_state=state,
            event_type=event_type,
            payload=payload,
            session_payload={
                key: value
                for key, value in session.items()
                if key not in {"state", "patient_state_version", "created_at", "updated_at"}
            },
            patient_state_version=int(session["patient_state_version"]),
        )

    async def list_events(self, session_id: str) -> list[SessionEvent]:
        if session_id in self._volatile:
            return list(self._events[session_id])
        try:
            return await self.delegate.list_events(session_id)
        except Exception as error:
            if not self._is_dependency_failure(error):
                raise
            self._activate_volatile(session_id, error)
            return list(self._events[session_id])

    async def write_json_artifact(self, namespace: str, payload: object) -> tuple[str, str]:
        parts = namespace.strip("/").split("/")
        session_id = parts[1] if len(parts) >= 2 and parts[0] == "sessions" else None
        if session_id in self._volatile:
            raise PersistenceUnavailableError("export unavailable for volatile session")
        try:
            return await self.delegate.write_json_artifact(namespace, payload)
        except Exception as error:
            if (
                session_id is None
                or not self._is_dependency_failure(error)
                or session_id not in self._sessions
            ):
                raise
            self._activate_volatile(session_id, error)
            raise PersistenceUnavailableError(
                "export unavailable after persistence failure"
            ) from error

    async def delete_session(self, session_id: str) -> bool:
        if session_id in self._volatile:
            raise PersistenceUnavailableError(
                "durable session deletion is unavailable while persistence is degraded"
            )
        return await self.delegate.delete_session(session_id)

    async def acquire_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        if session_id not in self._volatile:
            try:
                return await self.delegate.acquire_lease(session_id, owner_id, duration=duration)
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        current = self._leases.get(session_id)
        now = datetime.now(UTC)
        if current is not None and current[0] != owner_id and current[1] > now:
            return False
        self._leases[session_id] = (owner_id, now + duration)
        return True

    async def renew_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        if session_id not in self._volatile:
            try:
                return await self.delegate.renew_lease(session_id, owner_id, duration=duration)
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        current = self._leases.get(session_id)
        if current is None or current[0] != owner_id:
            return False
        self._leases[session_id] = (owner_id, datetime.now(UTC) + duration)
        return True

    async def release_lease(self, session_id: str, owner_id: str) -> None:
        if session_id not in self._volatile:
            try:
                await self.delegate.release_lease(session_id, owner_id)
                return
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        current = self._leases.get(session_id)
        if current is not None and current[0] == owner_id:
            self._leases.pop(session_id, None)

    async def begin_answer_idempotency(
        self, session_id: str, key_hash: str
    ) -> tuple[str, list[dict[str, Any]] | None]:
        if session_id not in self._volatile:
            try:
                return await self.delegate.begin_answer_idempotency(session_id, key_hash)
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        key = (session_id, key_hash)
        if key in self._idempotency:
            return self._idempotency[key]
        self._idempotency[key] = ("IN_PROGRESS", None)
        return "NEW", None

    async def complete_answer_idempotency(
        self, session_id: str, key_hash: str, response: list[dict[str, Any]]
    ) -> None:
        if session_id not in self._volatile:
            try:
                await self.delegate.complete_answer_idempotency(session_id, key_hash, response)
                return
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        self._idempotency[(session_id, key_hash)] = ("COMPLETED", response)

    async def abandon_answer_idempotency(self, session_id: str, key_hash: str) -> None:
        if session_id not in self._volatile:
            try:
                await self.delegate.abandon_answer_idempotency(session_id, key_hash)
                return
            except Exception as error:
                if not self._is_dependency_failure(error):
                    raise
                self._activate_volatile(session_id, error)
        self._idempotency.pop((session_id, key_hash), None)
