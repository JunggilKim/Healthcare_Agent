from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import orjson

from backend.app.application.proof_replay import replay_current_proofs
from backend.app.domain.sessions import SessionState
from backend.app.domain.trials import CompiledTrial
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

    @staticmethod
    def _branch_prefix(artifact_path: str) -> str:
        return str(PurePosixPath(artifact_path).with_suffix(""))

    @staticmethod
    def _available_branches(
        questions: dict[str, Any], *, question_id: str, branch_prefix: str
    ) -> list[dict[str, Any]]:
        branches = questions.get("branches", [])
        if not isinstance(branches, list):
            return []
        return [
            branch
            for branch in branches
            if isinstance(branch, dict)
            and branch.get("question_id") == question_id
            and str(PurePosixPath(str(branch.get("artifact_path", ""))).parent) == branch_prefix
        ]

    @classmethod
    def _hide_unreplayable_question(
        cls, payload: dict[str, Any], questions: dict[str, Any]
    ) -> bool:
        selection = payload.get("current_question")
        if not isinstance(selection, dict):
            return False
        selected = selection.get("selected")
        question_id = selected.get("question_id") if isinstance(selected, dict) else None
        if not isinstance(question_id, str):
            return False

        branch_prefix = payload.get("snapshot_branch_prefix")
        if not isinstance(branch_prefix, str):
            # Sessions created before branch ancestry was persisted cannot safely
            # replay a later answer. Keep their initial question available, but
            # fail closed after the first recorded patient-state change.
            if int(payload.get("patient_state_version", 0)) == 0:
                branch_prefix = "branches"
            else:
                branch_prefix = ""
        if cls._available_branches(questions, question_id=question_id, branch_prefix=branch_prefix):
            return False

        limited = dict(selection)
        limited.update(
            {
                "selected": None,
                "patient_facing_question": None,
                "deterministic_rationale": (
                    "이 스냅샷 데모에서 준비된 답변 경로를 모두 확인했습니다. "
                    "더 많은 질문을 이어가려면 라이브 모드로 새 분석을 시작하세요."
                ),
                "stop_reason": "SNAPSHOT_BRANCH_COVERAGE_EXHAUSTED",
                "top_alternatives": [],
            }
        )
        payload["current_question"] = limited
        return True

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
        snapshot_evaluation_date = str(initial.get("evaluation_date", ""))
        if evaluation_date.isoformat() != snapshot_evaluation_date:
            raise ValueError(
                "SNAPSHOT_EVALUATION_DATE_MISMATCH:"
                f"expected {snapshot_evaluation_date}, got {evaluation_date.isoformat()}"
            )
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
            "snapshot_branch_prefix": "branches",
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
            "snapshot_branch_prefix": payload["snapshot_branch_prefix"],
            "expires_at": payload["expires_at"],
        }
        payload.update(initial)
        payload.update(protected)
        questions = self._load(case_root / "questions.json")
        self._hide_unreplayable_question(payload, questions)
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
        payload = await self.store.read_session(session_id)
        if payload is None:
            return None
        payload = dict(payload)
        case_root = self.root / payload["snapshot_case_root"]
        questions = self._load(case_root / "questions.json")
        self._hide_unreplayable_question(payload, questions)
        return payload

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
        branch_prefix = str(payload.get("snapshot_branch_prefix", ""))
        branches = self._available_branches(
            questions, question_id=question_id, branch_prefix=branch_prefix
        )
        matching = [
            branch
            for branch in branches
            if (
                (
                    (unknown or declined)
                    and (bool(branch.get("unknown", False)) or bool(branch.get("declined", False)))
                )
                or (
                    not unknown
                    and not declined
                    and structured_value is not None
                    and branch.get("structured_value") == structured_value
                )
                or (
                    not unknown
                    and not declined
                    and structured_value is None
                    and branch.get("answer_text") == answer_text
                )
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
                "snapshot_branch_prefix",
                "expires_at",
            )
        }
        protected["snapshot_branch_prefix"] = self._branch_prefix(str(matching[0]["artifact_path"]))
        payload.update(branch_payload)
        payload.update(protected)
        self._hide_unreplayable_question(payload, questions)
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
        yield (
            "question_selected",
            {
                "sequence": event.sequence,
                "state": target.value,
                "slot_id": (
                    next_selection["selected"].get("slot_id")
                    if isinstance(next_selection.get("selected"), dict)
                    else None
                ),
            },
        )
        yield "completed", {"sequence": event.sequence, "state": target.value}

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if payload is None:
            return None
        case_root = self.root / payload["snapshot_case_root"]
        packets = [
            item
            for item in payload.get("proofs", [])
            if isinstance(item, dict) and item.get("nct_id") == nct_id
        ]
        if not packets:
            return None
        compiled_payload = orjson.loads((case_root / "compiled_trials.json").read_bytes())
        if not isinstance(compiled_payload, list):
            raise ValueError("snapshot compiled-trial artifact is not a list")
        compiled_trial = next(
            (
                CompiledTrial.model_validate(item)
                for item in compiled_payload
                if isinstance(item, dict) and item.get("nct_id") == nct_id
            ),
            None,
        )
        if compiled_trial is None:
            return None
        return replay_current_proofs(
            nct_id=nct_id,
            patient_state_version=int(payload.get("patient_state_version", 0)),
            facts=list(payload.get("facts", [])),
            conflicts=list(payload.get("conflicts", [])),
            compiled_trial=compiled_trial,
            proof_packets=packets,
        )

    async def export_report(self, session_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if payload is None or payload.get("export_available") is False:
            return None
        case_root = self.root / payload["snapshot_case_root"]
        reports = self._load(case_root / "reports.json")
        report = reports.get("initial", reports)
        export_payload = {
            "schema_version": "trial-opt-report-v1",
            "session_id": session_id,
            "patient_state_version": int(payload.get("patient_state_version", 0)),
            "mode": "snapshot",
            "data_timestamp": self.manifest().data_timestamp,
            "estimated_cost_usd": 0.0,
            "model_execution": "snapshot_cache_no_live_model_call",
            "medical_disclaimer": (
                "Research pre-screening only; the trial team makes the final determination."
            ),
            "report": report,
        }
        _, digest = await self.store.write_json_artifact(
            f"sessions/{session_id}/exports", export_payload
        )
        return {**export_payload, "artifact_sha256": digest}
