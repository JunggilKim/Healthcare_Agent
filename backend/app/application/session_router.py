from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any, Protocol

from backend.app.application.live_session_service import LiveSessionService
from backend.app.application.session_service import SnapshotSessionService
from backend.app.application.snapshot_replay_service import SnapshotReplayService
from backend.app.domain.sessions import SessionState
from backend.app.infrastructure.gcp_store import GcpSessionStore
from backend.app.infrastructure.local_store import LocalSessionStore
from backend.app.infrastructure.resilient_gcp_store import PersistenceResilientStore
from backend.app.settings import Settings

SessionStore = LocalSessionStore | GcpSessionStore | PersistenceResilientStore


class SessionService(Protocol):
    async def create_session(
        self,
        *,
        mode: str,
        seed_case_id: str,
        patient_text: str | None,
        evaluation_date: date,
        language: str,
    ) -> dict[str, Any]: ...

    async def authenticate(self, session_id: str, token: str) -> bool: ...

    def analyze(self, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...

    async def read_session(self, session_id: str) -> dict[str, Any] | None: ...

    def submit_answer(
        self,
        session_id: str,
        *,
        question_id: str,
        answer_text: str | None,
        structured_value: dict[str, object] | None,
        unknown: bool,
        declined: bool,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, object] | None: ...

    async def export_report(self, session_id: str) -> dict[str, object] | None: ...

    async def reset_session(self, session_id: str) -> dict[str, Any]: ...

    async def delete_session(self, session_id: str) -> bool: ...

    async def acquire_analysis_lease(self, session_id: str, owner_id: str) -> bool: ...

    async def renew_analysis_lease(self, session_id: str, owner_id: str) -> bool: ...

    async def release_analysis_lease(self, session_id: str, owner_id: str) -> None: ...

    async def begin_answer_idempotency(
        self, session_id: str, key_hash: str
    ) -> tuple[str, list[dict[str, Any]] | None]: ...

    async def complete_answer_idempotency(
        self, session_id: str, key_hash: str, response: list[dict[str, Any]]
    ) -> None: ...

    async def abandon_answer_idempotency(self, session_id: str, key_hash: str) -> None: ...


class RoutedSessionService:
    """Selects only between the frozen vertical slice, verified snapshot replay, and Live Mode."""

    def __init__(self, store: SessionStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.vertical = SnapshotSessionService(store)
        self.replay = SnapshotReplayService(store, settings.demo_snapshot_dir)
        self.live = (
            LiveSessionService(store, settings)
            if settings.allow_live_model_calls and settings.allow_live_ctgov_calls
            else None
        )

    async def create_session(
        self,
        *,
        mode: str,
        seed_case_id: str,
        patient_text: str | None,
        evaluation_date: date,
        language: str,
    ) -> dict[str, Any]:
        if mode == "snapshot":
            if patient_text is not None:
                raise ValueError("SNAPSHOT_ARBITRARY_TEXT_UNAVAILABLE")
            if seed_case_id == "S004" and not self.replay.has_case("S004"):
                return await self.vertical.create_session(
                    mode="snapshot",
                    seed_case_id=seed_case_id,
                    evaluation_date=evaluation_date,
                    language=language,
                )
            return await self.replay.create_session(
                mode="snapshot",
                seed_case_id=seed_case_id,
                evaluation_date=evaluation_date,
                language=language,
            )
        if mode != "live":
            raise ValueError("mode must be snapshot or live")
        if self.live is not None:
            return await self.live.create_session(
                mode="live",
                seed_case_id=seed_case_id,
                patient_text=patient_text,
                evaluation_date=evaluation_date,
                language=language,
            )
        if patient_text is None and seed_case_id == "S004":
            return await self.vertical.create_session(
                mode="live",
                seed_case_id="S004",
                evaluation_date=evaluation_date,
                language=language,
            )
        raise ValueError(
            "LIVE_DEPENDENCIES_DISABLED: set both ALLOW_LIVE_MODEL_CALLS and "
            "ALLOW_LIVE_CTGOV_CALLS after ADC/cost approval"
        )

    async def authenticate(self, session_id: str, token: str) -> bool:
        return await self.store.authenticate(session_id, token)

    async def _service(
        self, session_id: str
    ) -> SnapshotSessionService | SnapshotReplayService | LiveSessionService:
        payload = await self.store.read_session(session_id)
        if payload is None:
            raise KeyError(session_id)
        engine = payload.get("engine", "vertical_slice")
        if engine == "vertical_slice":
            return self.vertical
        if engine == "snapshot_replay":
            return self.replay
        if engine == "live" and self.live is not None:
            return self.live
        raise ValueError(f"SESSION_ENGINE_UNAVAILABLE:{engine}")

    async def analyze(self, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        service = await self._service(session_id)
        async for item in service.analyze(session_id):
            yield item

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        try:
            service = await self._service(session_id)
        except KeyError:
            return None
        return await service.read_session(session_id)

    async def submit_answer(
        self,
        session_id: str,
        *,
        question_id: str,
        answer_text: str | None,
        structured_value: dict[str, object] | None,
        unknown: bool,
        declined: bool,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        service = await self._service(session_id)
        async for item in service.submit_answer(
            session_id,
            question_id=question_id,
            answer_text=answer_text,
            structured_value=structured_value,
            unknown=unknown,
            declined=declined,
        ):
            yield item

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, object] | None:
        service = await self._service(session_id)
        return await service.read_proof(session_id, nct_id)

    async def export_report(self, session_id: str) -> dict[str, object] | None:
        service = await self._service(session_id)
        return await service.export_report(session_id)

    async def reset_session(self, session_id: str) -> dict[str, Any]:
        original = await self.store.read_session(session_id)
        if original is None:
            raise KeyError(session_id)
        mode = "live" if original.get("mode") in {"live", "hybrid_degraded"} else "snapshot"
        created = await self.create_session(
            mode=mode,
            seed_case_id=str(original.get("seed_case_id") or ""),
            patient_text=(
                str(original["patient_text"])
                if not original.get("seed_case_id") and original.get("patient_text")
                else None
            ),
            evaluation_date=date.fromisoformat(str(original["evaluation_date"])),
            language=str(original.get("language", "auto")),
        )
        child = await self.store.read_session(str(created["session_id"]))
        assert child is not None
        original_state = SessionState(str(original["state"]))
        original["reset_child_session_id"] = str(created["session_id"])
        await self.store.transition_and_append(
            session_id=session_id,
            expected_state=original_state,
            target_state=SessionState.RESET,
            event_type="SESSION_RESET",
            payload={"child_session_id": str(created["session_id"])},
            session_payload={
                key: value
                for key, value in original.items()
                if key not in {"state", "patient_state_version", "created_at", "updated_at"}
            },
            patient_state_version=int(original.get("patient_state_version", 0)),
        )
        child["parent_session_id"] = session_id
        await self.store.transition_and_append(
            session_id=str(created["session_id"]),
            expected_state=SessionState.CREATED,
            target_state=SessionState.CREATED,
            event_type="SESSION_RESET_LINKED",
            payload={"parent_session_id": session_id},
            session_payload={
                key: value
                for key, value in child.items()
                if key not in {"state", "patient_state_version", "created_at", "updated_at"}
            },
            patient_state_version=0,
        )
        return {**created, "parent_session_id": session_id}

    async def delete_session(self, session_id: str) -> bool:
        return await self.store.delete_session(session_id)

    async def acquire_analysis_lease(self, session_id: str, owner_id: str) -> bool:
        return await self.store.acquire_lease(session_id, owner_id, duration=timedelta(minutes=6))

    async def renew_analysis_lease(self, session_id: str, owner_id: str) -> bool:
        return await self.store.renew_lease(session_id, owner_id, duration=timedelta(minutes=6))

    async def release_analysis_lease(self, session_id: str, owner_id: str) -> None:
        await self.store.release_lease(session_id, owner_id)

    async def begin_answer_idempotency(
        self, session_id: str, key_hash: str
    ) -> tuple[str, list[dict[str, Any]] | None]:
        return await self.store.begin_answer_idempotency(session_id, key_hash)

    async def complete_answer_idempotency(
        self, session_id: str, key_hash: str, response: list[dict[str, Any]]
    ) -> None:
        await self.store.complete_answer_idempotency(session_id, key_hash, response)

    async def abandon_answer_idempotency(self, session_id: str, key_hash: str) -> None:
        await self.store.abandon_answer_idempotency(session_id, key_hash)
