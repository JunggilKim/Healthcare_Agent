from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.app.application.catalog import SlotCatalog, load_slot_catalog
from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.evidence import FactConflict, PatientFact, RetrievalHypothesis
from backend.app.domain.trials import CompiledTrial, ProtocolReviewArtifact, RawTrialRecord
from backend.app.engine.ast_validator import validate_ast_shape
from backend.app.settings import REPOSITORY_ROOT

FIXTURE_DIR = REPOSITORY_ROOT / "data" / "fixtures" / "vertical_slice"


@dataclass(frozen=True, slots=True)
class VerticalSliceFixture:
    patient_text: str
    raw_trial: RawTrialRecord
    eligibility_text: str
    compiled_trial: CompiledTrial
    review: ProtocolReviewArtifact
    facts: tuple[PatientFact, ...]
    hypotheses: tuple[RetrievalHypothesis, ...]
    conflicts: tuple[FactConflict, ...]
    enabled_acquisition_slots: tuple[str, ...]
    answers: dict[str, Any]
    source_texts: dict[str, str]


def _sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_seed(case_id: str) -> str:
    seed_path = REPOSITORY_ROOT / "data" / "seeds" / "synthetic-patients.json"
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    for topic in payload["topics"]:
        if topic["num"] == case_id:
            return str(topic["title"])
    raise ValueError(f"seed case not found: {case_id}")


def _validate_source_span(source_text: str, start: int, end: int, quote: str, digest: str) -> None:
    if source_text[start:end] != quote:
        raise ValueError("fixture source span does not resolve to its quote")
    if hashlib.sha256(quote.encode("utf-8")).hexdigest() != digest:
        raise ValueError("fixture source span hash mismatch")


def load_vertical_slice(catalog: SlotCatalog | None = None) -> VerticalSliceFixture:
    """Load and fully validate the only trial permitted in the frozen Phase-1 path."""

    slot_catalog = catalog or load_slot_catalog()
    slots = slot_catalog.by_id()
    manifest = yaml.safe_load((FIXTURE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    for filename, expected_hash in manifest["files"].items():
        if _sha256_bytes(FIXTURE_DIR / filename) != expected_hash:
            raise ValueError(f"vertical-slice manifest hash mismatch: {filename}")
    compact_path = FIXTURE_DIR / "NCT05239624.compact.json"
    compact = json.loads(compact_path.read_text(encoding="utf-8"))
    eligibility_text = str(compact["eligibility_criteria"])
    compiled_path = FIXTURE_DIR / "NCT05239624.criteria.json"
    compiled = CompiledTrial.model_validate_json(compiled_path.read_text(encoding="utf-8"))
    review_path = FIXTURE_DIR / "NCT05239624.review.json"
    review = ProtocolReviewArtifact.model_validate_json(review_path.read_text(encoding="utf-8"))

    calculated_compiled_hash = canonical_sha256(
        compiled.model_dump(mode="json", exclude={"content_hash"})
    )
    if compiled.content_hash != calculated_compiled_hash:
        raise ValueError("compiled fixture content hash mismatch")
    calculated_review_hash = canonical_sha256(
        review.model_dump(mode="json", exclude={"content_hash"})
    )
    if review.content_hash != calculated_review_hash:
        raise ValueError("manual review content hash mismatch")
    if review.compiled_protocol_hash != compiled.content_hash:
        raise ValueError("manual review is not bound to compiled protocol hash")
    if review.criterion_source_hashes != [item.source_text_sha256 for item in compiled.criteria]:
        raise ValueError("manual review criterion-source hashes do not match")
    if review.review_method != "MANUAL_FIXTURE" or review.reviewer_label != "specification_fixture":
        raise ValueError("Phase-1 fixture requires the specification manual review")
    if (
        hashlib.sha256(eligibility_text.encode("utf-8")).hexdigest()
        != compiled.eligibility_text_sha256
    ):
        raise ValueError("eligibility text hash mismatch")
    for criterion in compiled.criteria:
        span = criterion.source_span
        _validate_source_span(eligibility_text, span.start, span.end, span.quote, span.sha256)
        validate_ast_shape(criterion.ast, slots)

    patient_text = _load_seed("S004")
    evidence_payload = json.loads(
        (FIXTURE_DIR / "S004.initial_evidence.json").read_text(encoding="utf-8")
    )
    if (
        hashlib.sha256(patient_text.encode("utf-8")).hexdigest()
        != evidence_payload["source_text_sha256"]
    ):
        raise ValueError("S004 seed hash mismatch")
    facts = tuple(PatientFact.model_validate(item) for item in evidence_payload["facts"])
    for fact in facts:
        for span in fact.source_spans:
            _validate_source_span(patient_text, span.start, span.end, span.quote, span.sha256)
    hypotheses = tuple(
        RetrievalHypothesis.model_validate(item)
        for item in evidence_payload["retrieval_hypotheses"]
    )
    conflicts = tuple(FactConflict.model_validate(item) for item in evidence_payload["conflicts"])
    scope_payload = yaml.safe_load(
        (FIXTURE_DIR / "optimizer_scope.yaml").read_text(encoding="utf-8")
    )
    enabled_slots = tuple(scope_payload["enabled_acquisition_slots"])
    if enabled_slots != ("pathology.histology", "pathology.muscle_invasion"):
        raise ValueError("Phase-1 optimizer scope has changed")
    answers = json.loads((FIXTURE_DIR / "S004.answers.json").read_text(encoding="utf-8"))

    raw_trial = RawTrialRecord(
        nct_id=compact["nct_id"],
        api_version=compact["api_version"],
        retrieved_at=compact["retrieved_at"],
        source_json_sha256=_sha256_bytes(compact_path),
        version_holder=compact["version_holder"],
        last_update_post_date=compact["last_update_post_date"],
        overall_status=compact["overall_status"],
        study_type=compact["study_type"],
        official_title=compact["official_title"],
        brief_title=compact["brief_title"],
        conditions=compact["conditions"],
        keywords=compact["keywords"],
        brief_summary=None,
        detailed_description=None,
        eligibility_criteria=eligibility_text,
        sex=compact["sex"],
        minimum_age=compact["minimum_age"],
        maximum_age=None,
        healthy_volunteers=compact["healthy_volunteers"],
        phases=compact["phases"],
        intervention_names=compact["intervention_names"],
        locations=[],
        raw_gcs_uri=None,
    )
    return VerticalSliceFixture(
        patient_text=patient_text,
        raw_trial=raw_trial,
        eligibility_text=eligibility_text,
        compiled_trial=compiled,
        review=review,
        facts=facts,
        hypotheses=hypotheses,
        conflicts=conflicts,
        enabled_acquisition_slots=enabled_slots,
        answers=answers,
        source_texts={"seed:S004": patient_text},
    )
