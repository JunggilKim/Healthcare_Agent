from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.agents.answer_interpreter import proposal_from_structured_answer  # noqa: E402
from backend.app.agents.patient_evidence import PatientEvidenceAgent  # noqa: E402
from backend.app.agents.question_renderer import render_question  # noqa: E402
from backend.app.agents.report_renderer import validate_or_fallback_report  # noqa: E402
from backend.app.application.catalog import load_slot_catalog  # noqa: E402
from backend.app.application.interactive_loop import InteractiveTrialOptLoop  # noqa: E402
from backend.app.application.vertical_slice import load_vertical_slice  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes, load_yaml  # noqa: E402
from backend.app.domain.evidence import EligibilityContext, PatientState  # noqa: E402
from backend.app.domain.questions import (  # noqa: E402
    OptimizerRuntimeConfig,
    QuestionSelection,
)
from backend.app.domain.sessions import SessionAggregate  # noqa: E402
from backend.app.engine.multi_trial_optimizer import FullOptimizationState  # noqa: E402
from backend.app.engine.proof_verifier import build_verified_proof  # noqa: E402
from backend.app.engine.ranker import rank_trials  # noqa: E402
from backend.app.engine.trial_aggregator import aggregate_trial, is_trial_irrelevant  # noqa: E402
from backend.app.evaluation.corpus import build_release_corpus, load_release_corpus  # noqa: E402
from backend.app.infrastructure.cache import LocalModelResultCache  # noqa: E402
from backend.app.infrastructure.genai_client import create_google_cloud_genai_client  # noqa: E402
from backend.app.infrastructure.structured_generation import StructuredGenerator  # noqa: E402
from backend.app.infrastructure.usage_guard import (  # noqa: E402
    InMemoryUsageGuard,
    default_pricing_estimator,
)
from backend.app.retrieval.bm25 import build_trial_document  # noqa: E402
from backend.app.retrieval.embeddings import GeminiEmbeddingProvider  # noqa: E402
from backend.app.retrieval.models import RankedCandidate, RetrievalResult  # noqa: E402
from backend.app.settings import Settings  # noqa: E402

CASE_IDS = ("S004", "S008", "S001")


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _seed_text(case_id: str) -> str:
    payload = orjson.loads((REPOSITORY_ROOT / "data/seeds/synthetic-patients.json").read_bytes())
    return next(str(item["title"]) for item in payload["topics"] if item["num"] == case_id)


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


def _render_selection(selection: QuestionSelection, slots: dict[str, Any]) -> QuestionSelection:
    if selection.selected is None:
        return selection
    rendered = render_question(
        candidate=selection.selected,
        slot=slots[selection.selected.slot_id],
        deterministic_rationale=selection.deterministic_rationale,
    )
    return selection.model_copy(
        update={
            "patient_facing_question": rendered.text_ko,
            "deterministic_rationale": rendered.reason_ko,
        }
    )


def _state_payload(
    state: FullOptimizationState,
    selection: QuestionSelection,
    *,
    case_id: str,
    patient_text: str,
    degradation_codes: list[str],
) -> dict[str, object]:
    aggregate = state.aggregate
    top_id = aggregate.ranked_nct_ids[0] if aggregate.ranked_nct_ids else None
    return {
        "mode": "snapshot",
        "seed_case_id": case_id,
        "evaluation_date": aggregate.evaluation_date.isoformat(),
        "language": "en",
        "patient_text": patient_text,
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
                "data_timestamp": state.registry_data_versions[top_id],
            }
            if top_id
            else None
        ),
        "current_question": selection.model_dump(mode="json"),
        "ranked_nct_ids": aggregate.ranked_nct_ids,
        "trial_evaluations": {
            key: value.model_dump(mode="json") for key, value in aggregate.trial_evaluations.items()
        },
        "asked_slot_ids": aggregate.asked_slot_ids,
        "unavailable_slot_ids": aggregate.unavailable_slot_ids,
        "degradation_codes": degradation_codes,
        "export_available": True,
    }


def _branch_answer(branch: Any) -> tuple[str, dict[str, object] | None, bool, bool]:
    if branch.response_kind == "VALUE":
        assert branch.synthetic_value is not None
        return branch.label, branch.synthetic_value.model_dump(mode="json"), False, False
    if branch.response_kind == "UNKNOWN":
        return "unknown", None, True, False
    if branch.response_kind == "DECLINE":
        return "decline", None, False, True
    return branch.label, None, False, False


def _apply_branch(
    state: FullOptimizationState,
    selection: QuestionSelection,
    branch: Any,
    *,
    source_id: str,
    catalog: Any,
    answer_text_override: str | None = None,
    unknown_override: bool | None = None,
    declined_override: bool | None = None,
) -> tuple[FullOptimizationState, QuestionSelection, dict[str, object]]:
    candidate = selection.selected
    assert candidate is not None
    simulated = state.deep_copy_for_simulation()
    loop = InteractiveTrialOptLoop(simulated, catalog)
    answer_text, structured, unknown, declined = _branch_answer(branch)
    proposal = None
    if answer_text_override is not None:
        answer_text = answer_text_override
    elif structured is not None:
        answer_text, proposal = proposal_from_structured_answer(
            candidate=candidate,
            structured_value=structured,
            slot_catalog=catalog,
        )
    result = loop.submit_answer(
        candidate=candidate,
        answer_text=answer_text,
        source_id=source_id,
        asserted_at=simulated.evaluated_at,
        proposal=proposal,
    )
    next_selection = _render_selection(result.next_selection, simulated.slots)
    return (
        simulated,
        next_selection,
        {
            "answer_text": answer_text if answer_text_override is not None else (
                None if structured is not None else answer_text
            ),
            "structured_value": structured,
            "unknown": unknown if unknown_override is None else unknown_override,
            "declined": declined if declined_override is None else declined_override,
        },
    )


async def _embeddings(
    *,
    provider: GeminiEmbeddingProvider,
    dense_query: str,
    trials: list[Any],
    output_root: Path,
) -> None:
    documents = [build_trial_document(item) for item in trials]
    query_vector = await provider.embed_query(dense_query)
    document_vectors = await provider.embed_documents(documents)
    vectors: dict[str, list[float]] = {
        hashlib.sha256(
            f"RETRIEVAL_QUERY\0{dense_query}".encode()
        ).hexdigest(): query_vector.tolist()
    }
    arrays: dict[str, np.ndarray] = {"query": query_vector}
    records = [
        {
            "kind": "query",
            "nct_id": None,
            "text_sha256": hashlib.sha256(dense_query.encode()).hexdigest(),
            "vector_key": "query",
        }
    ]
    for trial, document, vector in zip(trials, documents, document_vectors, strict=True):
        digest = hashlib.sha256(f"RETRIEVAL_DOCUMENT\0{document}".encode()).hexdigest()
        vectors[digest] = vector.tolist()
        key = f"trial_{trial.nct_id}"
        arrays[key] = vector
        records.append(
            {
                "kind": "document",
                "nct_id": trial.nct_id,
                "text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "vector_key": key,
            }
        )
    _write(
        output_root / "embeddings.json",
        {"model": provider.model, "dimension": provider.dimension, "vectors": vectors},
    )
    np.savez_compressed(output_root / "embeddings.npz", **arrays)  # type: ignore[arg-type]
    _write(output_root / "embedding_records.json", records)


async def _materialize_case(
    *,
    case_id: str,
    corpus_root: Path,
    acquisition_root: Path,
    output_root: Path,
    patient_agent: PatientEvidenceAgent,
    embedding_provider: GeminiEmbeddingProvider,
    evaluation_date: date,
    evaluated_at: datetime,
    catalog: Any,
) -> dict[str, object]:
    case_corpus = corpus_root / "sessions" / case_id
    corpus = load_release_corpus(
        compiled_paths=[case_corpus / "compiled_trials.json"],
        raw_paths=[case_corpus / "raw_trials.json"],
        review_paths=[case_corpus / "reviews.json"],
    )
    retrieval = RetrievalResult.model_validate(
        orjson.loads((acquisition_root / "sessions" / case_id / "retrieval.json").read_bytes())
    )
    acquisition_manifest = orjson.loads((acquisition_root / "acquisition.json").read_bytes())
    case_manifest = next(
        item for item in acquisition_manifest["cases"] if item["case_id"] == case_id
    )
    if case_id == "S004":
        pinned = load_vertical_slice(catalog)
        current_candidates = {item.nct_id: item for item in retrieval.ranked_candidates}
        removed_nct_id = min(
            corpus.compiled_trials,
            key=lambda nct_id: (current_candidates[nct_id].retrieval_score, nct_id),
        )
        corpus = build_release_corpus(
            [item for nct_id, item in corpus.compiled_trials.items() if nct_id != removed_nct_id]
            + [pinned.compiled_trial],
            [item for nct_id, item in corpus.raw_trials.items() if nct_id != removed_nct_id]
            + [pinned.raw_trial],
            [item for nct_id, item in corpus.reviews.items() if nct_id != removed_nct_id]
            + [pinned.review],
        )
        pinned_candidate = RankedCandidate(
            nct_id=pinned.raw_trial.nct_id,
            registry_rank=1,
            bm25_rank=1,
            embedding_rank=1,
            exact_condition_match=True,
            lexical_rrf=1.0,
            full_rrf=1.0,
            retrieval_score=1.0,
            trial=pinned.raw_trial,
            compiled=True,
            compilation_status="VERIFIED",
        )
        ranked_candidates = [
            pinned_candidate,
            *[item for item in retrieval.ranked_candidates if item.nct_id != removed_nct_id][:19],
        ]
        retrieval = retrieval.model_copy(
            update={
                "ranked_candidates": ranked_candidates,
                "selected_for_compilation": [item.nct_id for item in ranked_candidates[:8]],
            }
        )
    patient_text = _seed_text(case_id)
    source_id = f"seed:{case_id}"
    if case_id == "S004":
        # The specification freezes this exact seed extraction independently of the
        # Phase-1 optimizer scope. The final corpus still uses the full optimizer.
        pinned = load_vertical_slice(catalog)
        patient_state = PatientState(
            confirmed_facts=list(pinned.facts),
            retrieval_hypotheses=list(pinned.hypotheses),
            conflicts=list(pinned.conflicts),
        )
        degraded = False
    else:
        extraction, degraded = await patient_agent.extract(
            patient_text=patient_text,
            source_id=source_id,
            language_hint="en",
            evaluation_date=evaluation_date,
            asserted_at=evaluated_at,
            session_id=f"snapshot-materialization:{case_id}",
        )
        patient_state = extraction.state
    context = EligibilityContext(
        facts=patient_state.confirmed_facts,
        conflicts=patient_state.conflicts,
    )
    source_texts = {source_id: patient_text}
    candidate_by_id = {item.nct_id: item for item in retrieval.ranked_candidates}
    session_id = f"snapshot-{case_id}-20260812"
    proofs_by_trial = {}
    evaluations = []
    for nct_id, compiled in corpus.compiled_trials.items():
        candidate = candidate_by_id[nct_id]
        packets = [
            build_verified_proof(
                session_id=session_id,
                patient_state_version=0,
                evaluation_date=evaluation_date,
                criterion=criterion,
                compiled_trial=compiled,
                review=corpus.reviews[nct_id],
                raw_trial=corpus.raw_trials[nct_id],
                registry_data_version=retrieval.registry_data_timestamp,
                eligibility_context=context,
                source_texts=source_texts,
                slots=catalog.by_id(),
                evaluated_at=evaluated_at,
            )
            for criterion in compiled.criteria
        ]
        proofs_by_trial[nct_id] = packets
        evaluations.append(
            aggregate_trial(
                session_id=session_id,
                patient_state_version=0,
                compiled_trial=compiled,
                raw_trial=corpus.raw_trials[nct_id],
                proofs=packets,
                retrieval_score=candidate.retrieval_score,
                irrelevant=is_trial_irrelevant(
                    retrieval_score=candidate.retrieval_score,
                    exact_condition_match=candidate.exact_condition_match,
                    compiled_trial=compiled,
                    facts=patient_state.confirmed_facts,
                ),
            )
        )
    ranked = rank_trials(evaluations)
    aggregate = SessionAggregate(
        session_id=session_id,
        mode="snapshot",
        evaluation_date=evaluation_date,
        patient_state_version=0,
        question_count=0,
        facts=patient_state.confirmed_facts,
        retrieval_hypotheses=patient_state.retrieval_hypotheses,
        conflicts=patient_state.conflicts,
        compiled_trials=corpus.compiled_trials,
        trial_evaluations={item.nct_id: item for item in ranked},
        ranked_nct_ids=[item.nct_id for item in ranked],
        asked_slot_ids=[],
        unavailable_slot_ids=[],
        current_question_id=None,
        config=_optimizer_config(),
    )
    state = FullOptimizationState(
        aggregate=aggregate,
        proofs_by_trial=proofs_by_trial,
        raw_trials=corpus.raw_trials,
        reviews=corpus.reviews,
        registry_data_versions={
            nct_id: retrieval.registry_data_timestamp for nct_id in corpus.raw_trials
        },
        source_texts=source_texts,
        slots=catalog.by_id(),
        evaluated_at=evaluated_at,
    )
    loop = InteractiveTrialOptLoop(state, catalog)
    selection = _render_selection(loop.prepare_next_question(), state.slots)
    if selection.selected is None:
        alternatives = [
            {
                "slot_id": item.slot_id,
                "utility": (
                    item.utility_components.final_utility
                    if item.utility_components is not None
                    else None
                ),
                "affected": len(item.affected),
            }
            for item in selection.top_alternatives
        ]
        raise RuntimeError(
            f"SNAPSHOT_FIRST_QUESTION_MISSING:{case_id}:{selection.stop_reason}:"
            f"alternatives={alternatives}"
        )
    if case_id == "S004" and selection.selected.slot_id != "pathology.histology":
        raise RuntimeError(
            f"SNAPSHOT_FIRST_QUESTION_UNEXPECTED:{case_id}:"
            f"{selection.selected.slot_id}:expected=pathology.histology"
        )
    degradation_codes = ["PATIENT_EXTRACTION_DETERMINISTIC_FALLBACK"] if degraded else []
    case_output = output_root / "sessions" / case_id
    _write(
        case_output / "initial.json",
        _state_payload(
            state,
            selection,
            case_id=case_id,
            patient_text=patient_text,
            degradation_codes=degradation_codes,
        ),
    )
    _write(
        case_output / "raw_trials.json",
        [item.model_dump(mode="json") for item in corpus.raw_trials.values()],
    )
    _write(
        case_output / "compiled_trials.json",
        [item.model_dump(mode="json") for item in corpus.compiled_trials.values()],
    )
    _write(
        case_output / "reviews.json",
        [item.model_dump(mode="json") for item in corpus.reviews.values()],
    )
    raw_api_root = case_output / "raw_api"
    raw_api_root.mkdir(parents=True, exist_ok=True)
    for nct_id in corpus.raw_trials:
        source = acquisition_root / "sessions" / case_id / "raw_api" / f"{nct_id}.json"
        if nct_id == "NCT05239624" and not source.is_file():
            source = REPOSITORY_ROOT / "data/fixtures/retrieval/S004/NCT05239624.raw.json"
        if not source.is_file():
            raise RuntimeError(f"SNAPSHOT_RAW_API_MISSING:{case_id}:{nct_id}")
        shutil.copyfile(source, raw_api_root / source.name)
    _write(case_output / "retrieval.json", retrieval.model_dump(mode="json"))
    _write(
        case_output / "proofs.json",
        {
            "proofs_by_trial": {
                key: [item.model_dump(mode="json") for item in values]
                for key, values in state.proofs_by_trial.items()
            }
        },
    )
    _write(
        case_output / "ranking.json",
        {
            "ranked_nct_ids": state.aggregate.ranked_nct_ids,
            "trial_evaluations": {
                key: value.model_dump(mode="json")
                for key, value in state.aggregate.trial_evaluations.items()
            },
        },
    )
    branch_rows = []
    first_branch_states: list[tuple[FullOptimizationState, QuestionSelection, str]] = []
    s004_branch_a: tuple[FullOptimizationState, QuestionSelection, str] | None = None
    for branch in selection.selected.branches:
        branch_id = branch.branch_id
        is_s004_branch_a = bool(
            case_id == "S004"
            and branch.synthetic_value is not None
            and getattr(branch.synthetic_value, "value", None) == "urothelial_carcinoma"
        )
        is_s004_branch_b = case_id == "S004" and branch.response_kind == "UNKNOWN"
        branch_state, next_selection, answer = _apply_branch(
            state,
            selection,
            branch,
            source_id=f"snapshot:{case_id}:answer:{branch_id}",
            catalog=catalog,
            answer_text_override=(
                "Existing pathology report confirms high-grade urothelial carcinoma."
                if is_s004_branch_a
                else (
                    "No pathology test has been performed; only the CT finding is available."
                    if is_s004_branch_b
                    else None
                )
            ),
            unknown_override=False if is_s004_branch_b else None,
            declined_override=True if is_s004_branch_b else None,
        )
        relative = f"branches/{branch_id}.json"
        _write(
            case_output / relative,
            _state_payload(
                branch_state,
                next_selection,
                case_id=case_id,
                patient_text=patient_text,
                degradation_codes=degradation_codes,
            ),
        )
        branch_rows.append(
            {
                "depth": 1,
                "question_id": selection.selected.question_id,
                "branch_id": branch_id,
                "artifact_path": relative,
                **answer,
            }
        )
        if next_selection.selected is not None:
            first_branch_states.append((branch_state, next_selection, branch_id))
            if is_s004_branch_a:
                s004_branch_a = (branch_state, next_selection, branch_id)
    if case_id == "S004":
        if s004_branch_a is None:
            raise RuntimeError("SNAPSHOT_S004_PINNED_BRANCH_A_MISSING")
        parent_state, second_selection, parent_id = s004_branch_a
        assert second_selection.selected is not None
        if second_selection.selected.slot_id != "pathology.muscle_invasion":
            raise RuntimeError(
                "SNAPSHOT_S004_SECOND_QUESTION_UNEXPECTED:"
                f"{second_selection.selected.slot_id}"
            )
        for branch in second_selection.selected.branches:
            branch_id = branch.branch_id
            branch_state, next_selection, answer = _apply_branch(
                parent_state,
                second_selection,
                branch,
                source_id=f"snapshot:{case_id}:answer:{parent_id}:{branch_id}",
                catalog=catalog,
            )
            relative = f"branches/{parent_id}/{branch_id}.json"
            _write(
                case_output / relative,
                _state_payload(
                    branch_state,
                    next_selection,
                    case_id=case_id,
                    patient_text=patient_text,
                    degradation_codes=degradation_codes,
                ),
            )
            branch_rows.append(
                {
                    "depth": 2,
                    "parent_branch_id": parent_id,
                    "question_id": second_selection.selected.question_id,
                    "branch_id": branch_id,
                    "artifact_path": relative,
                    **answer,
                }
            )
    _write(
        case_output / "questions.json",
        {
            "first_selection": selection.model_dump(mode="json"),
            "branches": branch_rows,
        },
    )
    top_id = state.aggregate.ranked_nct_ids[0]
    report = validate_or_fallback_report(
        evaluation=state.aggregate.trial_evaluations[top_id],
        decision_proofs=state.proofs_by_trial[top_id],
        proposal=None,
    )
    _write(
        case_output / "reports.json",
        {
            "initial": report.model_dump(mode="json"),
            "medical_disclaimer": (
                "Research pre-screening only; not diagnosis, medical advice, or final eligibility."
            ),
        },
    )
    _write(
        case_output / "experiment_summary.json",
        {
            "case_id": case_id,
            "trial_count": len(corpus.compiled_trials),
            "first_question_id": selection.selected.question_id,
            "first_slot_id": selection.selected.slot_id,
            "first_question_utility": selection.selected.utility_components.model_dump(mode="json")
            if selection.selected.utility_components
            else None,
            "patient_extraction_degraded": degraded,
            "opaque_criteria_count": sum(
                item.opaque for trial in corpus.compiled_trials.values() for item in trial.criteria
            ),
        },
    )
    ordered_trials = [corpus.raw_trials[nct_id] for nct_id in corpus.compiled_trials]
    await _embeddings(
        provider=embedding_provider,
        dense_query=str(case_manifest["dense_query"]),
        trials=ordered_trials,
        output_root=case_output,
    )
    return {
        "case_id": case_id,
        "trial_count": len(corpus.compiled_trials),
        "first_question_id": selection.selected.question_id,
        "first_slot_id": selection.selected.slot_id,
        "branch_count": len(branch_rows),
        "patient_extraction_degraded": degraded,
    }


async def materialize(args: argparse.Namespace) -> dict[str, object]:
    acquisition = orjson.loads((args.acquisition / "acquisition.json").read_bytes())
    evaluated_at = datetime.fromisoformat(str(acquisition["acquired_at"]))
    settings = Settings(
        google_cloud_project=args.project,
        google_cloud_location="global",
        allow_live_model_calls=True,
    )
    client = create_google_cloud_genai_client(settings)
    generator = StructuredGenerator(
        client=client,
        cache=LocalModelResultCache(args.cache),
        pricing=default_pricing_estimator(),
        usage_guard=InMemoryUsageGuard(),
    )
    catalog = load_slot_catalog()
    patient_agent = PatientEvidenceAgent(generator, catalog)
    embedding_provider = GeminiEmbeddingProvider(
        client, model="gemini-embedding-001", dimension=768
    )
    summaries = []
    for case_id in CASE_IDS:
        summary = await _materialize_case(
            case_id=case_id,
            corpus_root=args.corpus,
            acquisition_root=args.acquisition,
            output_root=args.output,
            patient_agent=patient_agent,
            embedding_provider=embedding_provider,
            evaluation_date=args.evaluation_date,
            evaluated_at=evaluated_at,
            catalog=catalog,
        )
        summaries.append(summary)
        print(orjson.dumps(summary).decode(), flush=True)
    artifact_hashes = {
        path.relative_to(args.output).as_posix(): _sha256(path)
        for path in sorted((args.output / "sessions").rglob("*"))
        if path.is_file()
    }
    data_timestamps = {str(item["data_timestamp"]) for item in acquisition["cases"]}
    if len(data_timestamps) != 1:
        raise RuntimeError("SNAPSHOT_CASE_DATA_TIMESTAMPS_DIFFER")
    snapshot_acquisition = {
        "schema_version": "trial-opt-live-acquisition-v1",
        "mode": "LIVE",
        "case_ids": list(CASE_IDS),
        "data_timestamp": next(iter(data_timestamps)),
        "source_candidate_acquisition_sha256": _sha256(args.acquisition / "acquisition.json"),
        "source_release_corpus_sha256": _sha256(args.corpus / "manifest.json"),
        "project_id": args.project,
        "git_sha": _git_sha(),
        "materialized_at": datetime.now(UTC).isoformat(),
        "cases": summaries,
        "artifact_sha256": artifact_hashes,
    }
    _write(args.output / "acquisition.json", snapshot_acquisition)
    return snapshot_acquisition


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize three-case release snapshot source from the reviewed corpus"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-date", type=date.fromisoformat, default=date(2026, 8, 11))
    parser.add_argument(
        "--cache",
        type=Path,
        default=REPOSITORY_ROOT / ".local_store/release-demo-model-cache",
    )
    parser.add_argument("--allow-live-materialization", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_materialization or os.environ.get("ALLOW_LIVE_MODEL_CALLS") != "true":
        raise SystemExit(
            "Live snapshot materialization requires --allow-live-materialization "
            "and ALLOW_LIVE_MODEL_CALLS=true"
        )
    if args.output.exists():
        raise SystemExit("OUTPUT_MUST_BE_A_FRESH_DIRECTORY")
    args.output.mkdir(parents=True)
    manifest = asyncio.run(materialize(args))
    cases = manifest["cases"]
    artifact_hashes = manifest["artifact_sha256"]
    if not isinstance(cases, list) or not isinstance(artifact_hashes, dict):
        raise RuntimeError("SNAPSHOT_MATERIALIZATION_MANIFEST_INVALID")
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "case_count": len(cases),
                "artifact_count": len(artifact_hashes),
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
