from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import orjson
from pydantic import Field, model_validator

from backend.app.domain.ast import CriterionAst
from backend.app.domain.base import StrictModel
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import SourceDirection
from backend.app.domain.evidence import SourceSpan
from backend.app.domain.trials import CompiledCriterion, CompiledTrial
from backend.app.evaluation.models import BenchmarkArtifact, PatientWorld, WorldFact


class AnnotationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"
    OPAQUE = "OPAQUE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewRole(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    ADJUDICATOR = "ADJUDICATOR"


class AnnotationAssignment(StrictModel):
    schema_version: Literal["trial-opt-annotation-assignment-v1"] = (
        "trial-opt-annotation-assignment-v1"
    )
    record_id: str
    world_id: str
    nct_id: str
    criterion_id: str
    split: Literal["development", "validation", "test"]
    evaluation_date: date
    compiled_protocol_hash: str
    criterion_source_hash: str
    source_direction: SourceDirection
    source_span: SourceSpan
    normalized_summary: str
    criterion_ast: CriterionAst
    required_slots: list[str]
    criticality: Literal["CRITICAL", "NONCRITICAL"]
    compiler_protocol_verified: bool
    compiler_opaque: bool
    facts: list[WorldFact]
    conflict_slots: list[str]
    unavailable_slots: list[str]
    template_narrative: str
    narrative: str
    narrative_language: Literal["en", "ko"]
    narrative_method: Literal["DETERMINISTIC_TEMPLATE", "FLASH_LITE_PARAPHRASE"]
    fact_span_map: dict[str, list[SourceSpan]]
    dual_review_required: bool
    rubric_version: Literal["dataset-a-rubric-v1"] = "dataset-a-rubric-v1"
    assignment_hash: str

    @model_validator(mode="after")
    def hash_and_identifiers_are_bound(self) -> AnnotationAssignment:
        if self.source_span.sha256 != self.criterion_source_hash:
            raise ValueError("ASSIGNMENT_SOURCE_HASH_MISMATCH")
        if self.source_span.quote.strip() == "":
            raise ValueError("ASSIGNMENT_SOURCE_QUOTE_EMPTY")
        if self.criterion_id.split(":", 1)[0] != self.nct_id:
            raise ValueError("ASSIGNMENT_CRITERION_TRIAL_MISMATCH")
        if self.assignment_hash != annotation_assignment_hash(self):
            raise ValueError("ASSIGNMENT_HASH_MISMATCH")
        return self


class AnnotationReview(StrictModel):
    schema_version: Literal["trial-opt-annotation-review-v1"] = "trial-opt-annotation-review-v1"
    record_id: str
    assignment_hash: str
    reviewer_alias: str = Field(min_length=1)
    role: ReviewRole
    revision: int = Field(ge=1)
    submitted_at: datetime
    blinded_to_system_output: Literal[True]
    verdict: AnnotationVerdict
    evidence_fact_ids: list[str]
    missing_slot_ids: list[str]
    safely_executable: bool
    explanation_supported: bool
    rationale: str = Field(min_length=1)
    disagreement_reason: str | None = None


class AdjudicatedAnnotation(StrictModel):
    schema_version: Literal["trial-opt-adjudicated-annotation-v1"] = (
        "trial-opt-adjudicated-annotation-v1"
    )
    record_id: str
    assignment_hash: str
    verdict: AnnotationVerdict
    evidence_fact_ids: list[str]
    missing_slot_ids: list[str]
    safely_executable: bool
    explanation_supported: bool
    reviewer_aliases: list[str]
    adjudicator_alias: str | None
    disagreement: bool
    finalized_at: datetime


def annotation_assignment_hash(assignment: AnnotationAssignment) -> str:
    payload = assignment.model_dump(mode="json", exclude={"assignment_hash"})
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _record_id(world_id: str, criterion_id: str) -> str:
    digest = hashlib.sha256(f"{world_id}:{criterion_id}".encode()).hexdigest()[:24]
    return f"ann_{digest}"


def _sample_key(seed: int, record_id: str) -> tuple[str, str]:
    return hashlib.sha256(f"{seed}:{record_id}".encode()).hexdigest(), record_id


def build_annotation_assignments(
    benchmark: BenchmarkArtifact,
    compiled_trials: list[CompiledTrial],
    *,
    seed: int,
    sample_size: int,
    dual_review_size: int,
    complete_world_bundles: bool = False,
) -> list[AnnotationAssignment]:
    if benchmark.seed != seed:
        raise ValueError("ANNOTATION_SEED_MUST_MATCH_BENCHMARK")
    if sample_size < 1 or dual_review_size < 0 or dual_review_size > sample_size:
        raise ValueError("ANNOTATION_SAMPLE_SIZE_INVALID")
    trials = {trial.nct_id: trial for trial in compiled_trials}
    criteria = {
        criterion.criterion_id: criterion
        for trial in compiled_trials
        for criterion in trial.criteria
    }
    if len(trials) != len(compiled_trials):
        raise ValueError("ANNOTATION_DUPLICATE_COMPILED_TRIAL")

    candidates: list[tuple[str, PatientWorld, CompiledCriterion]] = []
    seen: set[str] = set()
    for world in benchmark.worlds:
        trial = trials.get(world.nct_id)
        if trial is None:
            raise ValueError(f"ANNOTATION_COMPILED_TRIAL_MISSING:{world.nct_id}")
        if trial.content_hash != world.compiled_protocol_hash:
            raise ValueError(f"ANNOTATION_PROTOCOL_HASH_MISMATCH:{world.world_id}")
        fact_ids = {fact.fact_id for fact in world.facts}
        for truth in world.criterion_truth:
            compiled_criterion = criteria.get(truth.criterion_id)
            if compiled_criterion is None or compiled_criterion.nct_id != world.nct_id:
                raise ValueError(f"ANNOTATION_CRITERION_MISSING:{truth.criterion_id}")
            if not set(truth.evidence_fact_ids) <= fact_ids:
                raise ValueError(f"ANNOTATION_TRUTH_FACT_MISSING:{truth.criterion_id}")
            record_id = _record_id(world.world_id, truth.criterion_id)
            if record_id in seen:
                raise ValueError(f"ANNOTATION_DUPLICATE_RECORD:{record_id}")
            seen.add(record_id)
            candidates.append((record_id, world, compiled_criterion))
    if len(candidates) < sample_size:
        raise ValueError(
            f"ANNOTATION_SAMPLE_UNDERSIZED:available={len(candidates)} required={sample_size}"
        )
    candidates.sort(key=lambda item: _sample_key(seed, item[0]))
    if complete_world_bundles:
        by_world: dict[str, list[tuple[str, PatientWorld, CompiledCriterion]]] = {}
        for item in candidates:
            by_world.setdefault(item[1].world_id, []).append(item)
        selected = []
        for world_id in sorted(
            by_world,
            key=lambda value: _sample_key(seed, f"world:{value}"),
        ):
            selected.extend(by_world[world_id])
            if len(selected) >= sample_size:
                break
    else:
        selected = candidates[:sample_size]
    dual_ids = {
        item[0]
        for item in sorted(selected, key=lambda item: _sample_key(seed + 1, item[0]))[
            :dual_review_size
        ]
    }
    assignments: list[AnnotationAssignment] = []
    for record_id, world, criterion in selected:
        draft = AnnotationAssignment.model_construct(
            record_id=record_id,
            world_id=world.world_id,
            nct_id=world.nct_id,
            criterion_id=criterion.criterion_id,
            split=world.split,
            evaluation_date=world.evaluation_date,
            compiled_protocol_hash=world.compiled_protocol_hash,
            criterion_source_hash=criterion.source_text_sha256,
            source_direction=criterion.source_direction,
            source_span=criterion.source_span,
            normalized_summary=criterion.normalized_summary,
            criterion_ast=criterion.ast,
            required_slots=criterion.required_slots,
            criticality=criterion.criticality,
            compiler_protocol_verified=criterion.protocol_verified,
            compiler_opaque=criterion.opaque,
            facts=world.facts,
            conflict_slots=world.conflict_slots,
            unavailable_slots=world.unavailable_slots,
            template_narrative=world.template_narrative,
            narrative=world.narrative,
            narrative_language=world.narrative_language,
            narrative_method=world.narrative_method,
            fact_span_map=world.fact_span_map,
            dual_review_required=record_id in dual_ids,
            assignment_hash="",
        )
        assignments.append(
            AnnotationAssignment.model_validate(
                {
                    **draft.model_dump(mode="json", exclude={"assignment_hash"}),
                    "assignment_hash": annotation_assignment_hash(draft),
                }
            )
        )
    return sorted(assignments, key=lambda item: item.record_id)


def load_compiled_trials(paths: list[Path]) -> list[CompiledTrial]:
    raw_trials: list[object] = []
    for path in paths:
        payload = orjson.loads(path.read_bytes())
        if isinstance(payload, list):
            raw_trials.extend(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("trials"), list):
            raw_trials.extend(payload["trials"])
        elif isinstance(payload, dict) and "criteria" in payload:
            raw_trials.append(payload)
        elif isinstance(payload, dict):
            raw_trials.extend(payload.values())
        else:
            raise ValueError("ANNOTATION_COMPILED_TRIAL_SHAPE_INVALID")
    return [CompiledTrial.model_validate(item) for item in raw_trials]


def write_jsonl(path: Path, rows: Sequence[StrictModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"\n".join(canonical_json_bytes(row.model_dump(mode="json")) for row in rows) + b"\n"
    path.write_bytes(content)


def load_jsonl(path: Path, model: type[StrictModel]) -> list[StrictModel]:
    rows: list[StrictModel] = []
    for line_number, raw_line in enumerate(path.read_bytes().splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            rows.append(model.model_validate(orjson.loads(raw_line)))
        except Exception as exc:
            raise ValueError(f"ANNOTATION_JSONL_INVALID:{path}:{line_number}:{exc}") from exc
    return rows


def _latest_reviews(reviews: list[AnnotationReview]) -> list[AnnotationReview]:
    seen_revisions: set[tuple[str, str, ReviewRole, int]] = set()
    latest: dict[tuple[str, str, ReviewRole], AnnotationReview] = {}
    for review in reviews:
        revision_key = (review.record_id, review.reviewer_alias, review.role, review.revision)
        if revision_key in seen_revisions:
            raise ValueError("ANNOTATION_DUPLICATE_REVIEW_REVISION")
        seen_revisions.add(revision_key)
        key = revision_key[:3]
        previous = latest.get(key)
        if previous is None or review.revision > previous.revision:
            latest[key] = review
    return list(latest.values())


def _cohen_kappa(left: list[AnnotationVerdict], right: list[AnnotationVerdict]) -> float | None:
    if len(left) != len(right):
        raise ValueError("ANNOTATION_KAPPA_LENGTH_MISMATCH")
    if not left:
        return None
    observed = sum(a is b for a, b in zip(left, right, strict=True)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in AnnotationVerdict
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def adjudicate_annotations(
    assignments: list[AnnotationAssignment],
    reviews: list[AnnotationReview],
) -> tuple[list[AdjudicatedAnnotation], dict[str, object]]:
    assignment_by_id = {item.record_id: item for item in assignments}
    if len(assignment_by_id) != len(assignments):
        raise ValueError("ANNOTATION_DUPLICATE_ASSIGNMENT")
    latest = _latest_reviews(reviews)
    grouped: dict[str, dict[ReviewRole, list[AnnotationReview]]] = {}
    for review in latest:
        assignment = assignment_by_id.get(review.record_id)
        if assignment is None:
            raise ValueError(f"ANNOTATION_REVIEW_RECORD_UNKNOWN:{review.record_id}")
        if review.assignment_hash != assignment.assignment_hash:
            raise ValueError(f"ANNOTATION_REVIEW_HASH_MISMATCH:{review.record_id}")
        fact_ids = {fact.fact_id for fact in assignment.facts}
        if not set(review.evidence_fact_ids) <= fact_ids:
            raise ValueError(f"ANNOTATION_REVIEW_FACT_UNKNOWN:{review.record_id}")
        if not set(review.missing_slot_ids) <= set(assignment.required_slots):
            raise ValueError(f"ANNOTATION_REVIEW_SLOT_UNKNOWN:{review.record_id}")
        grouped.setdefault(review.record_id, {}).setdefault(review.role, []).append(review)

    gold: list[AdjudicatedAnnotation] = []
    dual_left: list[AnnotationVerdict] = []
    dual_right: list[AnnotationVerdict] = []
    incomplete: list[str] = []
    for assignment in assignments:
        roles = grouped.get(assignment.record_id, {})
        primary = roles.get(ReviewRole.PRIMARY, [])
        secondary = roles.get(ReviewRole.SECONDARY, [])
        adjudicators = roles.get(ReviewRole.ADJUDICATOR, [])
        if len(primary) != 1:
            incomplete.append(f"{assignment.record_id}:PRIMARY_REQUIRED")
            continue
        if assignment.dual_review_required and len(secondary) != 1:
            incomplete.append(f"{assignment.record_id}:SECONDARY_REQUIRED")
            continue
        if not assignment.dual_review_required and secondary:
            raise ValueError(f"ANNOTATION_UNASSIGNED_SECONDARY:{assignment.record_id}")
        reviewers = primary + secondary
        aliases = [item.reviewer_alias for item in reviewers]
        if len(set(aliases)) != len(aliases):
            raise ValueError(f"ANNOTATION_REVIEWERS_NOT_INDEPENDENT:{assignment.record_id}")
        disagreement = bool(secondary and primary[0].verdict is not secondary[0].verdict)
        if secondary:
            dual_left.append(primary[0].verdict)
            dual_right.append(secondary[0].verdict)
        if disagreement:
            if len(adjudicators) != 1:
                incomplete.append(f"{assignment.record_id}:ADJUDICATION_REQUIRED")
                continue
            selected = adjudicators[0]
            if selected.reviewer_alias in aliases:
                raise ValueError(f"ANNOTATION_ADJUDICATOR_NOT_INDEPENDENT:{assignment.record_id}")
            if not selected.disagreement_reason:
                raise ValueError(f"ANNOTATION_DISAGREEMENT_REASON_REQUIRED:{assignment.record_id}")
            adjudicator_alias: str | None = selected.reviewer_alias
        else:
            if adjudicators:
                raise ValueError(f"ANNOTATION_UNNEEDED_ADJUDICATION:{assignment.record_id}")
            selected = primary[0]
            adjudicator_alias = None
        gold.append(
            AdjudicatedAnnotation(
                record_id=assignment.record_id,
                assignment_hash=assignment.assignment_hash,
                verdict=selected.verdict,
                evidence_fact_ids=sorted(set(selected.evidence_fact_ids)),
                missing_slot_ids=sorted(set(selected.missing_slot_ids)),
                safely_executable=selected.safely_executable,
                explanation_supported=selected.explanation_supported,
                reviewer_aliases=sorted(aliases),
                adjudicator_alias=adjudicator_alias,
                disagreement=disagreement,
                finalized_at=selected.submitted_at,
            )
        )
    agreement = (
        sum(a is b for a, b in zip(dual_left, dual_right, strict=True)) / len(dual_left)
        if dual_left
        else None
    )
    summary: dict[str, object] = {
        "assignment_count": len(assignments),
        "dual_review_required": sum(item.dual_review_required for item in assignments),
        "completed_pairs": len(gold),
        "completed_dual_reviews": len(dual_left),
        "adjudicated_disagreements": sum(item.disagreement for item in gold),
        "raw_agreement": agreement,
        "cohen_kappa": _cohen_kappa(dual_left, dual_right),
        "incomplete": incomplete,
        "reviewer_aliases": sorted({item.reviewer_alias for item in latest}),
    }
    return sorted(gold, key=lambda item: item.record_id), summary
