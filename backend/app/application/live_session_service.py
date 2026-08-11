from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backend.app.agents.answer_interpreter import proposal_from_structured_answer
from backend.app.agents.patient_evidence import PatientEvidenceAgent
from backend.app.agents.prompts import render_prompt
from backend.app.agents.question_renderer import render_question
from backend.app.agents.report_renderer import validate_or_fallback_report
from backend.app.agents.retrieval_query import RetrievalQueryAgent
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.compilation_service import (
    CompilationWorkflowResult,
    ProtocolCompilationService,
)
from backend.app.application.interactive_loop import InteractiveTrialOptLoop
from backend.app.domain.canonical import canonical_json_bytes, canonical_sha256, load_yaml
from backend.app.domain.evidence import EligibilityContext
from backend.app.domain.proof import ProofPacket
from backend.app.domain.questions import (
    OptimizerRuntimeConfig,
    QuestionCandidate,
    QuestionSelection,
)
from backend.app.domain.ranking import TrialEvaluation
from backend.app.domain.rendering import AnswerInterpretationProposal, QuestionRenderProposal
from backend.app.domain.sessions import SessionAggregate, SessionState
from backend.app.domain.trials import CompiledTrial, ProtocolReviewArtifact, RawTrialRecord
from backend.app.engine.multi_trial_optimizer import FullOptimizationState
from backend.app.engine.proof_verifier import build_verified_proof
from backend.app.engine.ranker import rank_trials
from backend.app.engine.trial_aggregator import aggregate_trial, is_trial_irrelevant
from backend.app.infrastructure.cache import FirestoreModelResultCache, LocalModelResultCache
from backend.app.infrastructure.firestore_usage_guard import FirestoreUsageGuard
from backend.app.infrastructure.gcp_store import GcpSessionStore
from backend.app.infrastructure.genai_client import create_google_cloud_genai_client
from backend.app.infrastructure.local_artifacts import LocalArtifactStore
from backend.app.infrastructure.local_store import LocalSessionStore
from backend.app.infrastructure.resilient_gcp_store import PersistenceResilientStore
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)
from backend.app.infrastructure.usage_guard import InMemoryUsageGuard, default_pricing_estimator
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient, CtgovUnavailableError
from backend.app.retrieval.embeddings import GeminiEmbeddingProvider, RecordedEmbeddingProvider
from backend.app.retrieval.retriever import HybridRetriever
from backend.app.settings import REPOSITORY_ROOT, Settings

SessionStore = LocalSessionStore | GcpSessionStore | PersistenceResilientStore


def _seed_text(case_id: str) -> str:
    payload = json.loads(
        (REPOSITORY_ROOT / "data/seeds/synthetic-patients.json").read_text(encoding="utf-8")
    )
    for item in payload["topics"]:
        if item["num"] == case_id:
            return str(item["title"])
    raise ValueError(f"seed case not found: {case_id}")


def _optimizer_config() -> OptimizerRuntimeConfig:
    payload = load_yaml(REPOSITORY_ROOT / "config/question_optimizer.yaml")
    return OptimizerRuntimeConfig(
        top_k=payload["top_k"],
        max_questions=payload["default_max_questions"],
        hard_max_questions=payload["hard_max_questions"],
        max_branches=payload["max_branches"],
        stop_utility_threshold=payload["stop_utility_threshold"],
        stable_risk_reduction_threshold=payload["stable_risk_reduction_threshold"],
    )


def _unapproved_review(compiled: CompiledTrial, now: datetime) -> ProtocolReviewArtifact:
    payload: dict[str, Any] = {
        "review_id": f"review:{compiled.nct_id}:unapproved:{compiled.content_hash[:16]}",
        "nct_id": compiled.nct_id,
        "criterion_source_hashes": [item.source_text_sha256 for item in compiled.criteria],
        "compiled_protocol_hash": compiled.content_hash,
        "review_method": "MANUAL_FIXTURE",
        "reviewer_label": "unapproved_runtime_placeholder",
        "model_id": None,
        "prompt_version": None,
        "reviewed_at": now,
        "approved": False,
        "issues": [],
    }
    return ProtocolReviewArtifact(
        **payload,
        content_hash=canonical_sha256(payload),
    )


class LiveSessionService:
    """Bounded first-party Live Mode orchestrator with deterministic decision policy."""

    def __init__(self, store: SessionStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.catalog = load_slot_catalog()
        self._cold_session_semaphore = asyncio.Semaphore(2)
        client = create_google_cloud_genai_client(settings)
        session_cap = Decimal(str(settings.session_cost_cap_usd))
        daily_cap = Decimal(
            str(
                settings.daily_demo_cost_cap_usd
                if settings.app_env in {"demo", "prod"}
                else settings.daily_dev_cost_cap_usd
            )
        )
        total_cap = Decimal(str(settings.total_app_cost_cap_usd))
        firestore_client = (
            store.firestore
            if isinstance(store, (GcpSessionStore, PersistenceResilientStore))
            else None
        )
        usage_guard = (
            FirestoreUsageGuard(
                firestore_client,
                session_cap_usd=session_cap,
                daily_cap_usd=daily_cap,
                total_cap_usd=total_cap,
            )
            if firestore_client is not None
            else InMemoryUsageGuard(
                session_cap_usd=session_cap,
                daily_cap_usd=daily_cap,
                total_cap_usd=total_cap,
            )
        )
        self.generator = StructuredGenerator(
            client=client,
            cache=(
                FirestoreModelResultCache(firestore_client)
                if firestore_client is not None
                else LocalModelResultCache(settings.local_store_dir / "model-cache")
            ),
            pricing=default_pricing_estimator(),
            usage_guard=usage_guard,
        )
        self.patient_agent = PatientEvidenceAgent(self.generator, self.catalog)
        self.query_agent = RetrievalQueryAgent(self.generator)
        self.compiler = ProtocolCompilationService(self.generator, self.catalog)
        self.retriever = HybridRetriever(
            ctgov=ClinicalTrialsGovClient(
                LocalArtifactStore(settings.local_store_dir / "retrieval")
            ),
            embeddings=GeminiEmbeddingProvider(
                client,
                model=settings.gemini_embedding_model,
                dimension=settings.embedding_dim,
            ),
            snapshot_root=REPOSITORY_ROOT / "data/fixtures/retrieval/S004",
            snapshot_embeddings=RecordedEmbeddingProvider(
                REPOSITORY_ROOT / "data/fixtures/retrieval/S004/embeddings.json"
            ),
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
        if mode != "live":
            raise ValueError("LiveSessionService accepts Live Mode only")
        text = patient_text if patient_text is not None else _seed_text(seed_case_id)
        session_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "engine": "live",
            "mode": "live",
            "seed_case_id": seed_case_id or None,
            "evaluation_date": evaluation_date.isoformat(),
            "language": language,
            "patient_text": text,
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
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        }
        await self.store.create_session(session_id, token, payload)
        await self.store.append_event_without_transition(
            session_id=session_id,
            event_type="SESSION_CREATED",
            payload={"mode": "live", "seed_case_id": seed_case_id or None},
        )
        created = await self.store.read_session(session_id)
        assert created is not None
        return {
            "session_id": session_id,
            "session_token": token,
            "state": SessionState.CREATED.value,
            "mode": "live",
            "created_at": created["created_at"],
        }

    async def authenticate(self, session_id: str, token: str) -> bool:
        return await self.store.authenticate(session_id, token)

    async def _transition(
        self,
        payload: dict[str, Any],
        current: SessionState,
        target: SessionState,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
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

    def _serialize_state(
        self,
        payload: dict[str, Any],
        state: FullOptimizationState,
        selection: QuestionSelection,
    ) -> None:
        aggregate = state.aggregate
        top_id = aggregate.ranked_nct_ids[0] if aggregate.ranked_nct_ids else None
        payload.update(
            {
                "mode": aggregate.mode,
                "patient_state_version": aggregate.patient_state_version,
                "question_count": aggregate.question_count,
                "facts": [item.model_dump(mode="json") for item in aggregate.facts],
                "retrieval_hypotheses": [
                    item.model_dump(mode="json") for item in aggregate.retrieval_hypotheses
                ],
                "conflicts": [item.model_dump(mode="json") for item in aggregate.conflicts],
                "proofs": (
                    [item.model_dump(mode="json") for item in state.proofs_by_trial[top_id]]
                    if top_id
                    else []
                ),
                "criteria": (
                    [
                        {
                            "criterion_id": criterion.criterion_id,
                            "source_direction": criterion.source_direction.value,
                            "source_quote": criterion.source_span.quote,
                            "normalized_summary": criterion.normalized_summary,
                            "ast": criterion.ast.model_dump(mode="json"),
                        }
                        for criterion in aggregate.compiled_trials[top_id].criteria
                    ]
                    if top_id
                    else []
                ),
                "trial_evaluation": (
                    aggregate.trial_evaluations[top_id].model_dump(mode="json") if top_id else None
                ),
                "top_trial": (
                    {
                        "nct_id": top_id,
                        "title": state.raw_trials[top_id].brief_title,
                        "overall_status": state.raw_trials[top_id].overall_status,
                        "data_timestamp": state.registry_data_versions.get(top_id),
                    }
                    if top_id
                    else None
                ),
                "current_question": selection.model_dump(mode="json"),
                "ranked_nct_ids": aggregate.ranked_nct_ids,
                "trial_evaluations": {
                    key: value.model_dump(mode="json")
                    for key, value in aggregate.trial_evaluations.items()
                },
                "full_state": {
                    "aggregate": aggregate.model_dump(mode="json"),
                    "proofs_by_trial": {
                        key: [item.model_dump(mode="json") for item in values]
                        for key, values in state.proofs_by_trial.items()
                    },
                    "raw_trials": {
                        key: value.model_dump(mode="json")
                        for key, value in state.raw_trials.items()
                    },
                    "reviews": {
                        key: value.model_dump(mode="json") for key, value in state.reviews.items()
                    },
                    "registry_data_versions": state.registry_data_versions,
                    "source_texts": state.source_texts,
                    "evaluated_at": state.evaluated_at.isoformat(),
                    "dependency_stop_reason": state.dependency_stop_reason,
                },
            }
        )

    def _deserialize_state(self, payload: dict[str, Any]) -> FullOptimizationState:
        data = payload["full_state"]
        return FullOptimizationState(
            aggregate=SessionAggregate.model_validate(data["aggregate"]),
            proofs_by_trial={
                key: [ProofPacket.model_validate(item) for item in values]
                for key, values in data["proofs_by_trial"].items()
            },
            raw_trials={
                key: RawTrialRecord.model_validate(value)
                for key, value in data["raw_trials"].items()
            },
            reviews={
                key: ProtocolReviewArtifact.model_validate(value)
                for key, value in data["reviews"].items()
            },
            registry_data_versions=dict(data["registry_data_versions"]),
            source_texts=dict(data["source_texts"]),
            slots=self.catalog.by_id(),
            evaluated_at=datetime.fromisoformat(data["evaluated_at"]),
            dependency_stop_reason=data.get("dependency_stop_reason"),
        )

    async def _render_selection(
        self, selection: QuestionSelection, *, session_id: str
    ) -> QuestionSelection:
        candidate = selection.selected
        if candidate is None:
            return selection
        render_payload = {
            "question_id": candidate.question_id,
            "slot_id": candidate.slot_id,
            "action": candidate.action.value,
            "answer_type": candidate.answer_type,
            "deterministic_rationale": selection.deterministic_rationale,
        }
        proposal: QuestionRenderProposal | None = None
        try:
            proposal, _ = await self.generator.generate(
                model_id=self.settings.gemini_lite_model,
                task_name="question_renderer",
                prompt=render_prompt(
                    "question_renderer_v1.md",
                    render_payload=canonical_json_bytes(render_payload).decode(),
                ),
                prompt_version="1.0.0",
                output_schema_version="question-render-v1",
                slot_catalog_version=self.catalog.version,
                normalized_input=render_payload,
                output_model=QuestionRenderProposal,
                thinking_level="MINIMAL",
                max_output_tokens=500,
                max_attempts=2,
                session_id=session_id,
            )
        except StructuredGenerationUnavailable:
            pass
        rendered = render_question(
            candidate=candidate,
            slot=self.catalog.by_id()[candidate.slot_id],
            deterministic_rationale=selection.deterministic_rationale,
            proposal=proposal,
        )
        return selection.model_copy(
            update={
                "patient_facing_question": rendered.text_ko,
                "deterministic_rationale": rendered.reason_ko,
            }
        )

    async def _answer_proposal(
        self,
        *,
        candidate: QuestionCandidate,
        answer_text: str,
        session_id: str,
    ) -> AnswerInterpretationProposal | None:
        answer_payload = {
            "selected_slot": candidate.slot_id,
            "expected_type": candidate.answer_type,
            "answer_text": answer_text,
        }
        try:
            proposal, _ = await self.generator.generate(
                model_id=self.settings.gemini_lite_model,
                task_name="answer_interpreter",
                prompt=render_prompt(
                    "answer_interpreter_v1.md",
                    answer_payload=canonical_json_bytes(answer_payload).decode(),
                ),
                prompt_version="1.0.0",
                output_schema_version="answer-interpretation-v1",
                slot_catalog_version=self.catalog.version,
                normalized_input=answer_payload,
                output_model=AnswerInterpretationProposal,
                thinking_level="MINIMAL",
                max_output_tokens=800,
                max_attempts=2,
                session_id=session_id,
            )
            return proposal
        except StructuredGenerationUnavailable:
            return None

    async def analyze(self, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        payload = await self.store.read_session(session_id)
        if payload is None:
            raise KeyError(session_id)
        state = SessionState(payload["state"])
        if state is not SessionState.CREATED:
            yield "completed", {"sequence": 0, "state": state.value, "already_started": True}
            return
        now = datetime.now(UTC)
        event = await self._transition(
            payload, state, SessionState.INPUT_VALIDATING, "INPUT_VALIDATED", {}
        )
        yield "session_state", event
        state = SessionState.INPUT_VALIDATING
        event = await self._transition(
            payload,
            state,
            SessionState.PATIENT_EXTRACTING,
            "PATIENT_EXTRACTION_STARTED",
            {},
        )
        yield "stage_started", {**event, "stage": "Patient Evidence"}
        materialized, extraction_degraded = await self.patient_agent.extract(
            patient_text=payload["patient_text"],
            source_id=f"session:{session_id}:input",
            language_hint=payload["language"],
            evaluation_date=date.fromisoformat(payload["evaluation_date"]),
            asserted_at=now,
            session_id=session_id,
        )
        if extraction_degraded:
            payload["degradation_codes"].append("PATIENT_EXTRACTION_DETERMINISTIC_FALLBACK")
            yield (
                "degraded",
                {
                    "sequence": event["sequence"],
                    "state": state.value,
                    "degradation_codes": payload["degradation_codes"],
                },
            )
        patient_state = materialized.state
        payload.update(
            {
                "facts": [item.model_dump(mode="json") for item in patient_state.confirmed_facts],
                "retrieval_hypotheses": [
                    item.model_dump(mode="json") for item in patient_state.retrieval_hypotheses
                ],
                "conflicts": [item.model_dump(mode="json") for item in patient_state.conflicts],
                "source_texts": {f"session:{session_id}:input": payload["patient_text"]},
            }
        )
        state = SessionState.PATIENT_EXTRACTING
        event = await self._transition(
            payload,
            state,
            SessionState.RETRIEVING,
            "PATIENT_EXTRACTION_COMPLETED",
            {"fact_count": len(patient_state.confirmed_facts)},
        )
        yield "fact_extracted", event
        query = await self.query_agent.generate(
            patient_state, self.catalog.version, session_id=session_id
        )
        try:
            async with asyncio.timeout(11):
                retrieval = await self.retriever.retrieve(
                    query,
                    mode="live",
                    allow_snapshot_fallback=payload.get("seed_case_id") == "S004",
                )
        except TimeoutError:
            if payload.get("seed_case_id") == "S004":
                retrieval = await self.retriever.retrieve(query, mode="snapshot")
                retrieval = retrieval.model_copy(
                    update={
                        "mode": "hybrid_degraded",
                        "degradation_codes": [
                            *retrieval.degradation_codes,
                            "LIVE_RETRIEVAL_TIMEOUT_SNAPSHOT_USED",
                        ],
                    }
                )
            else:
                payload["mode"] = "hybrid_degraded"
                payload["degradation_codes"].append("LIVE_RETRIEVAL_TIMEOUT_NO_COMPATIBLE_SNAPSHOT")
                event = await self._transition(
                    payload,
                    SessionState.RETRIEVING,
                    SessionState.DEGRADED,
                    "DEPENDENCY_DEGRADED",
                    {"degradation_codes": payload["degradation_codes"]},
                )
                yield "degraded", event
                yield "completed", event
                return
        except CtgovUnavailableError:
            payload["mode"] = "hybrid_degraded"
            payload["degradation_codes"].append("CTGOV_UNAVAILABLE_NO_COMPATIBLE_SNAPSHOT")
            event = await self._transition(
                payload,
                SessionState.RETRIEVING,
                SessionState.DEGRADED,
                "DEPENDENCY_DEGRADED",
                {"degradation_codes": payload["degradation_codes"]},
            )
            yield "degraded", event
            yield "completed", event
            return
        payload["mode"] = retrieval.mode
        payload["retrieval"] = retrieval.model_dump(mode="json")
        payload["degradation_codes"].extend(retrieval.degradation_codes)
        state = SessionState.RETRIEVING
        event = await self._transition(
            payload,
            state,
            SessionState.CANDIDATES_READY,
            "RETRIEVAL_COMPLETED",
            {"candidate_count": len(retrieval.ranked_candidates)},
        )
        yield "retrieval_completed", event
        state = SessionState.CANDIDATES_READY
        event = await self._transition(
            payload, state, SessionState.COMPILING, "COMPILATION_STARTED", {}
        )
        yield "stage_started", {**event, "stage": "Protocol Compilation"}
        candidate_by_id = {item.nct_id: item for item in retrieval.ranked_candidates}
        compiled_trials: dict[str, CompiledTrial] = {}
        reviews: dict[str, ProtocolReviewArtifact] = {}
        raw_trials: dict[str, RawTrialRecord] = {}
        compilation_semaphore = asyncio.Semaphore(2)

        async def compile_one(nct_id: str) -> tuple[str, CompilationWorkflowResult]:
            candidate = candidate_by_id[nct_id]
            async with compilation_semaphore:
                result = await self.compiler.compile_and_review(
                    trial=candidate.trial,
                    evaluation_date=date.fromisoformat(payload["evaluation_date"]),
                    now=now,
                    session_id=session_id,
                )
            return nct_id, result

        async with self._cold_session_semaphore:
            compilation_results = await asyncio.gather(
                *(compile_one(nct_id) for nct_id in retrieval.selected_for_compilation)
            )
        for nct_id, result in compilation_results:
            candidate = candidate_by_id[nct_id]
            compiled = result.compilation.compiled_trial
            compiled_trials[nct_id] = compiled
            reviews[nct_id] = result.review_artifact or _unapproved_review(compiled, now)
            raw_trials[nct_id] = candidate.trial
            payload["degradation_codes"].extend(result.degradation_codes)
            yield (
                "trial_compiled",
                {
                    "sequence": event["sequence"],
                    "state": SessionState.COMPILING.value,
                    "nct_id": nct_id,
                    "protocol_verified": compiled.protocol_verified,
                },
            )
        state = SessionState.COMPILING
        event = await self._transition(
            payload, state, SessionState.EVALUATING, "COMPILATION_COMPLETED", {}
        )
        yield "stage_started", {**event, "stage": "Eligibility Proof"}
        proofs_by_trial: dict[str, list[ProofPacket]] = {}
        evaluations: list[TrialEvaluation] = []
        context = EligibilityContext(
            facts=patient_state.confirmed_facts, conflicts=patient_state.conflicts
        )
        source_texts = {f"session:{session_id}:input": payload["patient_text"]}
        for nct_id, compiled in compiled_trials.items():
            packets = [
                build_verified_proof(
                    session_id=session_id,
                    patient_state_version=0,
                    evaluation_date=date.fromisoformat(payload["evaluation_date"]),
                    criterion=criterion,
                    compiled_trial=compiled,
                    review=reviews[nct_id],
                    raw_trial=raw_trials[nct_id],
                    registry_data_version=retrieval.registry_data_timestamp,
                    eligibility_context=context,
                    source_texts=source_texts,
                    slots=self.catalog.by_id(),
                    evaluated_at=now,
                )
                for criterion in compiled.criteria
            ]
            proofs_by_trial[nct_id] = packets
            evaluations.append(
                aggregate_trial(
                    session_id=session_id,
                    patient_state_version=0,
                    compiled_trial=compiled,
                    raw_trial=raw_trials[nct_id],
                    proofs=packets,
                    retrieval_score=candidate_by_id[nct_id].retrieval_score,
                    irrelevant=is_trial_irrelevant(
                        retrieval_score=candidate_by_id[nct_id].retrieval_score,
                        exact_condition_match=candidate_by_id[nct_id].exact_condition_match,
                        compiled_trial=compiled,
                        facts=context.facts,
                    ),
                )
            )
        state = SessionState.EVALUATING
        event = await self._transition(
            payload,
            state,
            SessionState.VERIFYING,
            "CRITERIA_EVALUATED",
            {"trial_count": len(evaluations)},
        )
        yield "trial_evaluated", event
        state = SessionState.VERIFYING
        event = await self._transition(payload, state, SessionState.RANKING, "PROOFS_VERIFIED", {})
        yield "proof_verified", event
        ranked = rank_trials(evaluations)
        aggregate = SessionAggregate(
            session_id=session_id,
            mode=retrieval.mode,
            evaluation_date=date.fromisoformat(payload["evaluation_date"]),
            patient_state_version=0,
            question_count=0,
            facts=patient_state.confirmed_facts,
            retrieval_hypotheses=patient_state.retrieval_hypotheses,
            conflicts=patient_state.conflicts,
            compiled_trials=compiled_trials,
            trial_evaluations={item.nct_id: item for item in ranked},
            ranked_nct_ids=[item.nct_id for item in ranked],
            asked_slot_ids=[],
            unavailable_slot_ids=[],
            current_question_id=None,
            config=_optimizer_config(),
        )
        full_state = FullOptimizationState(
            aggregate=aggregate,
            proofs_by_trial=proofs_by_trial,
            raw_trials=raw_trials,
            reviews=reviews,
            registry_data_versions={
                nct_id: retrieval.registry_data_timestamp for nct_id in raw_trials
            },
            source_texts=source_texts,
            slots=self.catalog.by_id(),
            evaluated_at=now,
        )
        state = SessionState.RANKING
        event = await self._transition(
            payload,
            state,
            SessionState.QUESTION_SELECTING,
            "RANKING_UPDATED",
            {"ranked_nct_ids": aggregate.ranked_nct_ids},
        )
        yield "rankings_updated", event
        loop = InteractiveTrialOptLoop(full_state, self.catalog)
        selection = await self._render_selection(
            loop.prepare_next_question(), session_id=session_id
        )
        self._serialize_state(payload, full_state, selection)
        target = SessionState.QUESTION_READY if selection.selected else SessionState.COMPLETE
        event = await self._transition(
            payload,
            SessionState.QUESTION_SELECTING,
            target,
            "QUESTION_SELECTED" if selection.selected else "STOP_AND_REPORT",
            {
                "question_id": selection.selected.question_id if selection.selected else None,
                "stop_reason": selection.stop_reason,
            },
        )
        yield "question_selected", event
        yield "completed", event

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
        selected_question_id = (
            payload.get("current_question", {}).get("selected", {}).get("question_id")
        )
        if selected_question_id != question_id:
            raise ValueError("QUESTION_NOT_CURRENT")
        state = self._deserialize_state(payload)
        candidate = QuestionCandidate.model_validate(payload["current_question"]["selected"])
        event = await self._transition(
            payload,
            SessionState(payload["state"]),
            SessionState.ANSWER_INTERPRETING,
            "ANSWER_RECEIVED",
            {"question_id": question_id},
        )
        yield "stage_started", {**event, "stage": "Answer Interpretation"}
        safe_text = "decline" if declined else "unknown" if unknown else (answer_text or "")
        loop = InteractiveTrialOptLoop(state, self.catalog)
        proposal: AnswerInterpretationProposal | None
        if structured_value is not None:
            safe_text, proposal = proposal_from_structured_answer(
                candidate=candidate,
                structured_value=structured_value,
                slot_catalog=self.catalog,
            )
        else:
            proposal = (
                None
                if unknown or declined
                else await self._answer_proposal(
                    candidate=candidate,
                    answer_text=safe_text,
                    session_id=session_id,
                )
            )
        result = loop.submit_answer(
            candidate=candidate,
            answer_text=safe_text,
            source_id=f"session:{session_id}:answer:{state.aggregate.patient_state_version + 1}",
            asserted_at=datetime.now(UTC),
            proposal=proposal,
        )
        next_selection = await self._render_selection(result.next_selection, session_id=session_id)
        payload["patient_state_version"] = state.aggregate.patient_state_version
        event = await self._transition(
            payload,
            SessionState.ANSWER_INTERPRETING,
            SessionState.REEVALUATING,
            "ANSWER_INTERPRETED",
            {"changed_criterion_ids": result.changed_criterion_ids},
        )
        yield "trial_evaluated", event
        event = await self._transition(
            payload,
            SessionState.REEVALUATING,
            SessionState.QUESTION_SELECTING,
            "RANKING_UPDATED",
            {"rank_deltas": [item.model_dump(mode="json") for item in result.rank_deltas]},
        )
        yield "rankings_updated", event
        self._serialize_state(payload, state, next_selection)
        target = SessionState.QUESTION_READY if next_selection.selected else SessionState.COMPLETE
        event = await self._transition(
            payload,
            SessionState.QUESTION_SELECTING,
            target,
            "QUESTION_SELECTED" if next_selection.selected else "STOP_AND_REPORT",
            {
                "question_id": (
                    next_selection.selected.question_id if next_selection.selected else None
                ),
                "stop_reason": next_selection.stop_reason,
            },
        )
        yield "question_selected", event
        yield "completed", event

    async def read_proof(self, session_id: str, nct_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if payload is None or "full_state" not in payload:
            return None
        packets = payload["full_state"]["proofs_by_trial"].get(nct_id)
        if packets is None:
            return None
        compiled = payload["full_state"]["aggregate"].get("compiled_trials", {}).get(nct_id)
        return {
            "nct_id": nct_id,
            "trial_evaluation": payload.get("trial_evaluations", {}).get(nct_id),
            "criteria": compiled.get("criteria", []) if isinstance(compiled, dict) else [],
            "proof_packets": packets,
            "registry": payload["full_state"]["raw_trials"].get(nct_id),
        }

    async def export_report(self, session_id: str) -> dict[str, object] | None:
        payload = await self.store.read_session(session_id)
        if (
            payload is None
            or payload.get("export_available") is False
            or "full_state" not in payload
        ):
            return None
        ranked = list(payload.get("ranked_nct_ids", []))
        if not ranked:
            return None
        top_id = str(ranked[0])
        evaluation = TrialEvaluation.model_validate(payload["trial_evaluations"][top_id])
        proofs = [
            ProofPacket.model_validate(item)
            for item in payload["full_state"]["proofs_by_trial"][top_id]
        ]
        report = validate_or_fallback_report(
            evaluation=evaluation,
            decision_proofs=proofs,
            proposal=None,
        )
        export_payload = {
            "schema_version": "trial-opt-report-v1",
            "session_id": session_id,
            "mode": payload["mode"],
            "evaluation_date": payload["evaluation_date"],
            "data_timestamp": payload["full_state"]["registry_data_versions"].get(top_id),
            "ranked_nct_ids": ranked,
            "trial_evaluations": payload.get("trial_evaluations", {}),
            "remaining_unknowns": payload.get("current_question"),
            "estimated_cost_usd": payload.get("estimated_cost_usd"),
            "report": report.model_dump(mode="json"),
            "medical_disclaimer": (
                "Research pre-screening only; not diagnosis, medical advice, or final eligibility."
            ),
        }
        _, digest = await self.store.write_json_artifact(
            f"sessions/{session_id}/exports", export_payload
        )
        return {**export_payload, "artifact_sha256": digest}
