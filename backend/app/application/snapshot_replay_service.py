from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson

from backend.app.domain.sessions import SessionState
from backend.app.infrastructure.gcp_store import GcpSessionStore
from backend.app.infrastructure.local_store import LocalSessionStore
from backend.app.infrastructure.resilient_gcp_store import PersistenceResilientStore
from backend.app.infrastructure.snapshot_loader import SnapshotManifest, load_verified_snapshot

SessionStore = LocalSessionStore | GcpSessionStore | PersistenceResilientStore


class SnapshotReplayService:
    """Hash-verified replay adapter for the frozen S004/S008/S001 release snapshot."""

    def __init__(self, store: SessionStore, snapshot_root: Path) -> None:
        self.store = store
        self.root = snapshot_root

    def manifest(self) -> SnapshotManifest:
        return load_verified_snapshot(self.root, require_complete=True)

    def has_case(self, case_id: str) -> bool:
        try:
            manifest = self.manifest()
        except ValueError:
            return False
        return any(case.case_id == case_id and case.complete for case in manifest.cases)

    def _case_root(self, case_id: str) -> Path:
        if not self.has_case(case_id):
            raise ValueError(f"SNAPSHOT_CASE_UNAVAILABLE:{case_id}")
        return self.root / "sessions" / case_id

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        payload = orjson.loads(path.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError(f"snapshot artifact is not an object: {path.name}")
        return payload

    async def create_session(
        self,
        *,
        mode: str,
        seed_case_id: str,
        evaluation_date: date,
        language: str,
    ) -> dict[str, Any]:
        if mode != "snapshot":
            raise ValueError("SnapshotReplayService accepts Snapshot Mode only")
        case_root = self._case_root(seed_case_id)
        initial = self._load(case_root / "initial.json")
        session_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "engine": "snapshot_replay",
            "mode": "snapshot",
            "seed_case_id": seed_case_id,
            "evaluation_date": evaluation_date.isoformat(),
            "language": language,
            "patient_text": str(initial.get("patient_text", "")),
            "patient_state_version": 0,
            "question_count": 0,
            "facts": [],
            "retrieval_hypotheses": [],
            "conflicts": [],
            "proofs": [],
            "criteria": [],
            "trial_evaluation": None,
            "current_question": None,
            "degradation_codes": [],
            "snapshot_case_root": str(case_root.relative_to(self.root)),
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        }
        await self.store.create_session(session_id, token, payload)
        await self.store.append_event_without_transition(
            session_id=session_id,
            event_type="SESSION_CREATED",
            payload={"mode": "snapshot", "seed_case_id": seed_case_id},
        )
        created = await self.store.read_session(session_id)
        assert created is not None
        return {
            "session_id": session_id,
            "session_token": token,
            "state": SessionState.CREATED.value,
            "mode": "snapshot",
            "created_at": created["created_at"],
        }

    async def authenticate(self, session_id: str, token: str) -> bool:
        return await self.store.authenticate(session_id, token)

    async def analyze(self, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        payload = await self.store.read_session(session_id)
        if payload is None:
            raise KeyError(session_id)
        state = SessionState(payload["state"])
        if state is not SessionState.CREATED:
            yield "completed", {"sequence": 0, "state": state.value, "already_started": True}
            return
        case_root = self.root / payload["snapshot_case_root"]
        initial = self._load(case_root / "initial.json")
        protected = {
            "session_id": payload["session_id"],
            "engine": payload["engine"],
            "mode": "snapshot",
            "seed_case_id": payload["seed_case_id"],
            "evaluation_date": payload["evaluation_date"],
            "language": payload["language"],
            "snapshot_case_root": payload["snapshot_case_root"],
            "expires_at": payload["expires_at"],
        }
        payload.update(initial)
        payload.update(protected)
        selection = payload.get("current_question") or {}
        target = (
            SessionState.QUESTION_READY
            if isinstance(selection, dict) and selection.get("selected")
            else SessionState.COMPLETE
        )
        event = await self.store.transition_and_append(
            session_id=session_id,
            expected_state=SessionState.CREATED,
            target_state=target,
            event_type="SNAPSHOT_REPLAYED",
            payload={"case_id": payload["seed_case_id"], "target_state": target.value},
            session_payload=payload,
            patient_state_version=int(payload.get("patient_state_version", 0)),
        )
        stages = (
            ("fact_extracted", "Patient Evidence"),
            ("retrieval_completed", "Trial Retrieval"),
            ("trial_compiled", "Protocol Compilation"),
            ("trial_evaluated", "Eligibility Proof"),
            ("proof_verified", "Proof Verification"),
            ("rankings_updated", "Ranking"),
            ("question_selected", "Next Question Optimization"),
        )
        for event_name, stage in stages:
            yield (
                event_name,
                {
                    "sequence": event.sequence,
                    "state": target.value,
                    "stage": stage,
                    "snapshot_replay": True,
                },
            )
        yield "completed", {"sequence": event.sequence, "state": target.value}

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        return await self.store.read_session(session_id)

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
        payload = await self.store.read_session(session_id)
        if payload is None:
            raise KeyError(session_id)
        selected = (payload.get("current_question") or {}).get("selected") or {}
        if selected.get("question_id") != question_id:
            raise ValueError("QUESTION_NOT_CURRENT")
        current_state = SessionState(payload["state"])
        case_root = self.root / payload["snapshot_case_root"]
        questions = self._load(case_root / "questions.json")
        branches = questions.get("branches", [])
        matching = [
            branch
            for branch in branches
            if branch.get("question_id") == question_id
            and bool(branch.get("unknown", False)) == unknown
            and bool(branch.get("declined", False)) == declined
            and (
                unknown
                or declined
                or (
                    structured_value is not None
                    and branch.get("structured_value") == structured_value
                )
                or (structured_value is None and branch.get("answer_text") == answer_text)
            )
        ]
        if len(matching) != 1:
            raise ValueError("SNAPSHOT_BRANCH_UNAVAILABLE")
        branch_path = case_root / str(matching[0]["artifact_path"])
        branch_payload = self._load(branch_path)
        protected = {
            key: payload[key]
            for key in (
                "session_id",
                "engine",
                "mode",
                "seed_case_id",
                "evaluation_date",
                "language",
                "snapshot_case_root",
                "expires_at",
            )
        }
        payload.update(branch_payload)
        payload.update(protected)
        next_selection = payload.get("current_question") or {}
        target = (
            SessionState.QUESTION_READY
            if isinstance(next_selection, dict) and next_selection.get("selected")
            else SessionState.COMPLETE
        )
        event = await self.store.transition_and_append(
            session_id=session_id,
            expected_state=current_state,
            target_state=target,
            event_type="SNAPSHOT_BRANCH_REPLAYED",
            payload={"question_id": question_id, "branch_id": matching[0]["branch_id"]},
            session_payload=payload,
            patient_state_version=int(payload.get("patient_state_version", 0)),
        )
        yield "trial_evaluated", {"sequence": event.sequence, "state": target.value}
        yield "rankings_updated", {"sequence": event.sequence, "state": target.value}
        yield "question_selected", {"sequence": event.sequence, "state": target.value}
        yield "completed", {"sequence": event.sequence, "state": target.value}

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if payload is None:
            return None
        case_root = self.root / payload["snapshot_case_root"]
        proofs = self._load(case_root / "proofs.json")
        packets = proofs.get("proofs_by_trial", {}).get(nct_id)
        return {"nct_id": nct_id, "proof_packets": packets} if packets is not None else None

    async def export_report(self, session_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if payload is None or payload.get("export_available") is False:
            return None
        case_root = self.root / payload["snapshot_case_root"]
        reports = self._load(case_root / "reports.json")
        return {
            "schema_version": "trial-opt-report-v1",
            "session_id": session_id,
            "mode": "snapshot",
            "report": reports,
            "disclaimer": (
                "Research pre-screening only; not diagnosis, medical advice, or final eligibility."
            ),
        }
