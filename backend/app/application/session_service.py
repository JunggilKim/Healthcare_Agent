from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.app.agents.report_renderer import validate_or_fallback_report
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.state_machine import validate_transition
from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.evidence import EligibilityContext, FactConflict, PatientFact
from backend.app.domain.proof import ProofPacket
from backend.app.domain.questions import QuestionSelection
from backend.app.domain.ranking import TrialEvaluation
from backend.app.domain.sessions import SessionState
from backend.app.engine.proof_verifier import build_verified_proof
from backend.app.engine.question_optimizer import OptimizationState, select_next_action
from backend.app.engine.trial_aggregator import aggregate_trial
from backend.app.infrastructure.gcp_store import GcpSessionStore
from backend.app.infrastructure.local_store import LocalSessionStore


class SnapshotSessionService:
    """Frozen Phase-1 orchestrator for the S004/NCT05239624 path only."""

    def __init__(self, store: LocalSessionStore | GcpSessionStore) -> None:
        self.store = store
        self.fixture = load_vertical_slice()
        self.catalog = load_slot_catalog()
        self.slots = self.catalog.by_id()

    async def create_session(
        self,
        *,
        mode: str,
        seed_case_id: str,
        evaluation_date: date,
        language: str,
    ) -> dict[str, Any]:
        if mode not in {"snapshot", "live"} or seed_case_id != "S004":
            raise ValueError("Phase-1 supports only the frozen Snapshot S004 path")
        degraded_live = mode == "live"
        session_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "mode": "hybrid_degraded" if degraded_live else "snapshot",
            "engine": "vertical_slice",
            "seed_case_id": "S004",
            "evaluation_date": evaluation_date.isoformat(),
            "language": language,
            "patient_text": self.fixture.patient_text,
            "question_count": 0,
            "facts": [],
            "retrieval_hypotheses": [],
            "conflicts": [],
            "proofs": [],
            "trial_evaluation": None,
            "top_trial": {
                "nct_id": self.fixture.raw_trial.nct_id,
                "title": self.fixture.raw_trial.brief_title,
                "overall_status": self.fixture.raw_trial.overall_status,
                "data_timestamp": "2026-08-11T09:00:06Z",
            },
            "current_question": None,
            "asked_slot_ids": [],
            "unavailable_slot_ids": [],
            "source_texts": {"seed:S004": self.fixture.patient_text},
            "degradation_codes": (
                ["LIVE_DEPENDENCIES_DISABLED_SNAPSHOT_USED"] if degraded_live else []
            ),
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        }
        await self.store.create_session(session_id, token, payload)
        await self.store.append_event_without_transition(
            session_id=session_id,
            event_type="SESSION_CREATED",
            payload={"mode": payload["mode"], "seed_case_id": "S004"},
        )
        created = await self.store.read_session(session_id)
        assert created is not None
        return {
            "session_id": session_id,
            "session_token": token,
            "state": SessionState.CREATED.value,
            "mode": mode,
            "created_at": created["created_at"],
        }

    async def authenticate(self, session_id: str, token: str) -> bool:
        return await self.store.authenticate(session_id, token)

    async def _transition(
        self,
        *,
        payload: dict[str, Any],
        current: SessionState,
        target: SessionState,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        validate_transition(current, target)
        event = await self.store.transition_and_append(
            session_id=payload["session_id"],
            expected_state=current,
            target_state=target,
            event_type=event_type,
            payload=event_payload,
            session_payload=payload,
            patient_state_version=int(payload.get("patient_state_version", 0)),
        )
        return {
            "sequence": event.sequence,
            "state": target.value,
            "event_type": event_type,
            **event_payload,
        }

    def _build_proofs(
        self,
        *,
        session_id: str,
        patient_state_version: int,
        evaluation_date: date,
        facts: list[PatientFact],
        conflicts: list[FactConflict],
        source_texts: dict[str, str],
    ) -> list[ProofPacket]:
        context = EligibilityContext(facts=facts, conflicts=conflicts)
        return [
            build_verified_proof(
                session_id=session_id,
                patient_state_version=patient_state_version,
                evaluation_date=evaluation_date,
                criterion=criterion,
                compiled_trial=self.fixture.compiled_trial,
                review=self.fixture.review,
                raw_trial=self.fixture.raw_trial,
                registry_data_version="2026-08-11T09:00:06",
                eligibility_context=context,
                source_texts=source_texts,
                slots=self.slots,
                evaluated_at=datetime.now(UTC),
            )
            for criterion in self.fixture.compiled_trial.criteria
        ]

    def _optimizer_state(self, payload: dict[str, Any]) -> OptimizationState:
        facts = [PatientFact.model_validate(item) for item in payload["facts"]]
        conflicts = [FactConflict.model_validate(item) for item in payload["conflicts"]]
        proofs = [ProofPacket.model_validate(item) for item in payload["proofs"]]
        evaluation = TrialEvaluation.model_validate(payload["trial_evaluation"])
        return OptimizationState(
            session_id=payload["session_id"],
            patient_state_version=int(payload["patient_state_version"]),
            evaluation_date=date.fromisoformat(payload["evaluation_date"]),
            facts=facts,
            conflicts=conflicts,
            source_texts=dict(payload["source_texts"]),
            compiled_trial=self.fixture.compiled_trial,
            review=self.fixture.review,
            raw_trial=self.fixture.raw_trial,
            registry_data_version="2026-08-11T09:00:06",
            proofs=proofs,
            trial_evaluation=evaluation,
            slots=self.slots,
            enabled_acquisition_slots=self.fixture.enabled_acquisition_slots,
            unavailable_slot_ids=set(payload["unavailable_slot_ids"]),
            asked_slot_ids=list(payload["asked_slot_ids"]),
            question_count=int(payload["question_count"]),
        )

    async def analyze(self, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        payload = await self.store.read_session(session_id)
        if payload is None:
            raise KeyError(session_id)
        state = SessionState(payload["state"])
        if state is not SessionState.CREATED:
            yield "completed", {"sequence": 0, "state": state.value, "already_started": True}
            return

        if payload.get("degradation_codes"):
            yield (
                "degraded",
                {
                    "sequence": 0,
                    "state": SessionState.CREATED.value,
                    "mode": payload["mode"],
                    "degradation_codes": payload["degradation_codes"],
                },
            )

        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.INPUT_VALIDATING,
            event_type="INPUT_VALIDATED",
            event_payload={"seed_case_id": "S004"},
        )
        yield "session_state", event
        state = SessionState.INPUT_VALIDATING
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.PATIENT_EXTRACTING,
            event_type="INPUT_VALIDATED",
            event_payload={
                "source_text_sha256": (
                    "c1efaf597b5c9a5900865e4bff125932b47f0ab3e108f3a4c62c30ac4426055b"
                )
            },
        )
        yield "stage_started", {**event, "stage": "Patient Evidence"}
        state = SessionState.PATIENT_EXTRACTING
        payload["facts"] = [item.model_dump(mode="json") for item in self.fixture.facts]
        payload["retrieval_hypotheses"] = [
            item.model_dump(mode="json") for item in self.fixture.hypotheses
        ]
        payload["conflicts"] = [item.model_dump(mode="json") for item in self.fixture.conflicts]
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.RETRIEVING,
            event_type="PATIENT_EXTRACTION_COMPLETED",
            event_payload={"fact_count": len(self.fixture.facts), "hypothesis_count": 1},
        )
        yield "fact_extracted", event
        state = SessionState.RETRIEVING
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.CANDIDATES_READY,
            event_type="RETRIEVAL_COMPLETED",
            event_payload={"candidate_count": 1, "nct_ids": ["NCT05239624"]},
        )
        yield "retrieval_completed", event
        state = SessionState.CANDIDATES_READY
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.COMPILING,
            event_type="PROTOCOL_COMPILED",
            event_payload={"nct_id": "NCT05239624", "criterion_count": 7},
        )
        yield "trial_compiled", event
        state = SessionState.COMPILING
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.EVALUATING,
            event_type="PROTOCOL_REVIEWED",
            event_payload={"review_id": self.fixture.review.review_id, "approved": True},
        )
        yield "stage_started", {**event, "stage": "Eligibility Proof"}
        state = SessionState.EVALUATING
        facts = [PatientFact.model_validate(item) for item in payload["facts"]]
        proofs = self._build_proofs(
            session_id=session_id,
            patient_state_version=0,
            evaluation_date=date.fromisoformat(payload["evaluation_date"]),
            facts=facts,
            conflicts=[],
            source_texts=dict(payload["source_texts"]),
        )
        payload["proofs"] = [proof.model_dump(mode="json") for proof in proofs]
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.VERIFYING,
            event_type="CRITERION_EVALUATED",
            event_payload={"criterion_count": 7},
        )
        yield "trial_evaluated", event
        state = SessionState.VERIFYING
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.RANKING,
            event_type="PROOF_VERIFIED",
            event_payload={"proof_count": 7, "all_replay_checks_passed": True},
        )
        yield "proof_verified", event
        state = SessionState.RANKING
        evaluation = aggregate_trial(
            session_id=session_id,
            patient_state_version=0,
            compiled_trial=self.fixture.compiled_trial,
            raw_trial=self.fixture.raw_trial,
            proofs=proofs,
            retrieval_score=1.0,
        )
        payload["trial_evaluation"] = evaluation.model_dump(mode="json")
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.QUESTION_SELECTING,
            event_type="RANKING_UPDATED",
            event_payload={"ranked_nct_ids": ["NCT05239624"]},
        )
        yield "rankings_updated", event
        state = SessionState.QUESTION_SELECTING
        selection = select_next_action(self._optimizer_state(payload))
        payload["current_question"] = selection.model_dump(mode="json")
        scored_event = await self.store.append_event_without_transition(
            session_id=session_id,
            event_type="QUESTION_CANDIDATES_SCORED",
            payload={"candidate_count": len(selection.top_alternatives)},
        )
        yield (
            "stage_progress",
            {
                "sequence": scored_event.sequence,
                "state": state.value,
                "candidate_count": len(selection.top_alternatives),
            },
        )
        event = await self._transition(
            payload=payload,
            current=state,
            target=SessionState.QUESTION_READY,
            event_type="QUESTION_SELECTED",
            event_payload={"slot_id": selection.selected.slot_id if selection.selected else None},
        )
        yield "question_selected", event
        yield (
            "completed",
            {
                "sequence": event["sequence"],
                "state": SessionState.QUESTION_READY.value,
            },
        )

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
        if SessionState(payload["state"]) is not SessionState.QUESTION_READY:
            raise ValueError("QUESTION_NOT_CURRENT")
        selection = QuestionSelection.model_validate(payload["current_question"])
        if selection.selected is None or selection.selected.question_id != question_id:
            raise ValueError("QUESTION_NOT_CURRENT")
        slot_id = selection.selected.slot_id
        current = SessionState.QUESTION_READY
        event = await self._transition(
            payload=payload,
            current=current,
            target=SessionState.ANSWER_INTERPRETING,
            event_type="ANSWER_RECORDED",
            event_payload={"question_id": question_id, "slot_id": slot_id},
        )
        yield "stage_started", {**event, "stage": "Answer Interpretation"}
        current = SessionState.ANSWER_INTERPRETING
        branches = self.fixture.answers["pathology_histology"]
        if structured_value is not None:
            branch_value = branches["branch_a"]["fact"]["value"]
            if structured_value != branch_value:
                raise ValueError("SNAPSHOT_BRANCH_UNAVAILABLE")
            answer_text = branches["branch_a"]["answer_text"]
        if (
            slot_id == "pathology.histology"
            and answer_text == branches["branch_a"]["answer_text"]
            and not unknown
            and not declined
        ):
            fact = PatientFact.model_validate(branches["branch_a"]["fact"])
            payload["facts"] = [
                item for item in payload["facts"] if item["slot_id"] != "pathology.histology"
            ] + [fact.model_dump(mode="json")]
            payload["source_texts"][fact.source_spans[0].source_id] = answer_text
            answer_fact_ids = [fact.fact_id]
        elif (
            (
                slot_id == "pathology.histology"
                and answer_text == branches["branch_b"]["answer_text"]
            )
            or unknown
            or declined
        ):
            payload["unavailable_slot_ids"] = sorted(
                set(payload["unavailable_slot_ids"]) | {slot_id}
            )
            answer_fact_ids = []
        else:
            raise ValueError("SNAPSHOT_BRANCH_UNAVAILABLE")
        payload["asked_slot_ids"] = [*list(payload["asked_slot_ids"]), slot_id]
        payload["question_count"] = int(payload["question_count"]) + 1
        payload["patient_state_version"] = int(payload["patient_state_version"]) + 1
        event = await self._transition(
            payload=payload,
            current=current,
            target=SessionState.REEVALUATING,
            event_type="PATIENT_STATE_VERSION_INCREMENTED",
            event_payload={
                "patient_state_version": payload["patient_state_version"],
                "answer_fact_ids": answer_fact_ids,
            },
        )
        yield "fact_extracted", event
        current = SessionState.REEVALUATING
        facts = [PatientFact.model_validate(item) for item in payload["facts"]]
        conflicts = [FactConflict.model_validate(item) for item in payload["conflicts"]]
        proofs = self._build_proofs(
            session_id=session_id,
            patient_state_version=int(payload["patient_state_version"]),
            evaluation_date=date.fromisoformat(payload["evaluation_date"]),
            facts=facts,
            conflicts=conflicts,
            source_texts=dict(payload["source_texts"]),
        )
        payload["proofs"] = [proof.model_dump(mode="json") for proof in proofs]
        event = await self._transition(
            payload=payload,
            current=current,
            target=SessionState.VERIFYING,
            event_type="CRITERION_EVALUATED",
            event_payload={"affected_slot_id": slot_id},
        )
        yield "trial_evaluated", event
        current = SessionState.VERIFYING
        event = await self._transition(
            payload=payload,
            current=current,
            target=SessionState.RANKING,
            event_type="PROOF_VERIFIED",
            event_payload={
                "proof_count": 7,
                "patient_state_version": payload["patient_state_version"],
            },
        )
        yield "proof_verified", event
        current = SessionState.RANKING
        before = TrialEvaluation.model_validate(payload["trial_evaluation"])
        evaluation = aggregate_trial(
            session_id=session_id,
            patient_state_version=int(payload["patient_state_version"]),
            compiled_trial=self.fixture.compiled_trial,
            raw_trial=self.fixture.raw_trial,
            proofs=proofs,
            retrieval_score=1.0,
        )
        payload["trial_evaluation"] = evaluation.model_dump(mode="json")
        event = await self._transition(
            payload=payload,
            current=current,
            target=SessionState.QUESTION_SELECTING,
            event_type="RANKING_UPDATED",
            event_payload={
                "before_rank": 1,
                "after_rank": 1,
                "before_decision": before.decision.value,
                "after_decision": evaluation.decision.value,
                "changed_slot_id": slot_id,
            },
        )
        yield "rankings_updated", event
        current = SessionState.QUESTION_SELECTING
        next_selection = select_next_action(self._optimizer_state(payload))
        payload["current_question"] = next_selection.model_dump(mode="json")
        event = await self._transition(
            payload=payload,
            current=current,
            target=(
                SessionState.QUESTION_READY if next_selection.selected else SessionState.COMPLETE
            ),
            event_type=("QUESTION_SELECTED" if next_selection.selected else "SESSION_COMPLETED"),
            event_payload={
                "slot_id": next_selection.selected.slot_id if next_selection.selected else None,
                "stop_reason": next_selection.stop_reason,
            },
        )
        yield "question_selected" if next_selection.selected else "completed", event
        yield "completed", {"sequence": event["sequence"], "state": event["state"]}

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        payload = await self.store.read_session(session_id)
        if payload is None:
            return None
        payload.pop("source_texts", None)
        payload["criteria"] = [
            {
                "criterion_id": criterion.criterion_id,
                "source_direction": criterion.source_direction,
                "source_quote": criterion.source_span.quote,
                "normalized_summary": criterion.normalized_summary,
                "ast": criterion.ast.model_dump(mode="json"),
            }
            for criterion in self.fixture.compiled_trial.criteria
        ]
        return payload

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, Any] | None:
        payload = await self.store.read_session(session_id)
        if payload is None or nct_id != "NCT05239624" or not payload.get("proofs"):
            return None
        return {
            "trial_evaluation": payload["trial_evaluation"],
            "criteria": [
                item.model_dump(mode="json") for item in self.fixture.compiled_trial.criteria
            ],
            "proof_packets": payload["proofs"],
            "registry": self.fixture.raw_trial.model_dump(mode="json"),
        }

    async def export_report(self, session_id: str) -> dict[str, Any] | None:
        payload = await self.store.read_session(session_id)
        if payload is None or payload.get("trial_evaluation") is None:
            return None
        evaluation = TrialEvaluation.model_validate(payload["trial_evaluation"])
        proofs = [ProofPacket.model_validate(item) for item in payload["proofs"]]
        report = validate_or_fallback_report(
            evaluation=evaluation,
            decision_proofs=proofs,
            proposal=None,
        )
        export_payload = {
            "schema_version": "trial-opt-report-v1",
            "session_id": session_id,
            "patient_state_version": int(payload["patient_state_version"]),
            "mode": payload["mode"],
            "data_timestamp": "2026-08-11T09:00:06Z",
            "estimated_cost_usd": 0.0,
            "model_execution": "snapshot_cache_no_live_model_call",
            "medical_disclaimer": (
                "Research pre-screening only; the trial team makes the final determination."
            ),
            "report": report.model_dump(mode="json"),
        }
        _, digest = await self.store.write_json_artifact(
            f"sessions/{session_id}/exports", export_payload
        )
        return {**export_payload, "artifact_sha256": digest}
