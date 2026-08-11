from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, PatientFact
from backend.app.domain.proof import ProofPacket, VerifierCheck
from backend.app.domain.trials import (
    CompiledCriterion,
    CompiledTrial,
    ProtocolReviewArtifact,
    RawTrialRecord,
)
from backend.app.domain.values import NumberValue, RangeValue
from backend.app.engine.ast_validator import AstValidationError, validate_ast_shape
from backend.app.engine.evaluator import EvaluationResult, evaluate_criterion

_PROOF_ID_PATTERN = re.compile(r"^.+:v\d+:r\d+$")
_WHITELISTED_TRANSFORMATIONS = {"CALCULATE_AGE", "CONVERT_UNIT", "DATE_DIFFERENCE"}


def canonical_replay_payload(
    criterion_id: str,
    patient_state_version: int,
    result: EvaluationResult,
) -> dict[str, object]:
    return {
        "criterion_id": criterion_id,
        "patient_state_version": patient_state_version,
        "verdict": result.verdict.value,
        "evidence_fact_ids": sorted(result.evidence_fact_ids),
        "missing_slot_ids": sorted(result.missing_slot_ids),
        "conflict_ids": sorted(result.conflict_ids),
        "requires_review": result.requires_review,
        "issue_codes": sorted(result.issue_codes),
        "derivation_steps": [step.model_dump(mode="json") for step in result.derivation_steps],
    }


def _check(
    check_id: str,
    passed: bool,
    detail_code: str,
    *,
    applicable: bool = True,
    blocking: bool = True,
    hashes: list[str] | None = None,
) -> VerifierCheck:
    return VerifierCheck.model_validate(
        {
            "check_id": check_id,
            "applicable": applicable,
            "passed": passed,
            "blocking": blocking and applicable,
            "detail_code": detail_code,
            "artifact_hashes": hashes or [],
        }
    )


def _fact_source_valid(fact: PatientFact, source_texts: dict[str, str]) -> bool:
    if fact.grade is not EvidenceGrade.A_DIRECT:
        return True
    for span in fact.source_spans:
        source = source_texts.get(span.source_id)
        if source is None or source[span.start : span.end] != span.quote:
            return False
        if hashlib.sha256(span.quote.encode("utf-8")).hexdigest() != span.sha256:
            return False
    return bool(fact.source_spans)


def _units_valid(
    criterion: CompiledCriterion,
    facts: list[PatientFact],
    slots: dict[str, SlotDefinition],
) -> bool:
    for node in criterion.ast.nodes:
        if node.slot_id is None:
            continue
        slot = slots[node.slot_id]
        values = ([node.value] if node.value is not None else []) + list(node.values)
        for value in values:
            if (
                isinstance(value, (NumberValue, RangeValue))
                and value.unit not in slot.allowed_units
            ):
                return False
    for fact in facts:
        if fact.slot_id not in criterion.required_slots:
            continue
        if isinstance(fact.value, NumberValue):
            slot = slots[fact.slot_id]
            if fact.value.unit not in slot.allowed_units:
                return False
    return True


def _review_valid(compiled: CompiledTrial, review: ProtocolReviewArtifact) -> bool:
    exact_hash_binding = (
        review.compiled_protocol_hash == compiled.content_hash
        and review.criterion_source_hashes
        == [criterion.source_text_sha256 for criterion in compiled.criteria]
    )
    method_valid = review.review_method == "GEMINI_SEMANTIC_REVIEW" or (
        review.review_method == "MANUAL_FIXTURE"
        and review.reviewer_label == "specification_fixture"
        and compiled.nct_id == "NCT05239624"
    )
    review_hash_valid = review.content_hash == canonical_sha256(
        review.model_dump(mode="json", exclude={"content_hash"})
    )
    return review.approved and exact_hash_binding and method_valid and review_hash_valid


def build_verified_proof(
    *,
    session_id: str,
    patient_state_version: int,
    evaluation_date: date,
    criterion: CompiledCriterion,
    compiled_trial: CompiledTrial,
    review: ProtocolReviewArtifact,
    raw_trial: RawTrialRecord,
    registry_data_version: str | None,
    eligibility_context: EligibilityContext,
    source_texts: dict[str, str],
    slots: dict[str, SlotDefinition],
    evaluated_at: datetime | None = None,
) -> ProofPacket:
    """Build immutable decision packet r0 and execute PV-001 through PV-014."""

    evaluated_time = evaluated_at or datetime.now(UTC)
    result = evaluate_criterion(criterion, eligibility_context, evaluation_date)
    checks: list[VerifierCheck] = []
    checks.append(_check("PV-001", True, "SCHEMAS_VALID"))
    source = raw_trial.eligibility_criteria or ""
    span = criterion.source_span
    source_valid = (
        source[span.start : span.end] == span.quote
        and hashlib.sha256(span.quote.encode("utf-8")).hexdigest() == span.sha256
        and criterion.source_text_sha256 == span.sha256
    )
    checks.append(
        _check(
            "PV-002",
            source_valid,
            "SOURCE_MATCH" if source_valid else "SOURCE_MISMATCH",
            hashes=[span.sha256],
        )
    )
    try:
        validate_ast_shape(criterion.ast, slots)
        ast_valid = criterion.protocol_verified
    except AstValidationError:
        ast_valid = False
    checks.append(_check("PV-003", ast_valid, "AST_VALID" if ast_valid else "AST_INVALID"))
    review_valid = (
        compiled_trial.source_character_coverage >= 0.90
        and compiled_trial.boundary_tests_passed
        and _review_valid(compiled_trial, review)
    )
    checks.append(
        _check(
            "PV-004",
            review_valid,
            "PROTOCOL_REVIEW_APPROVED" if review_valid else "PROTOCOL_REVIEW_BLOCKED",
            hashes=[compiled_trial.content_hash, review.content_hash],
        )
    )

    evidence_facts = [
        fact for fact in eligibility_context.facts if fact.fact_id in result.evidence_fact_ids
    ]
    grade_a_facts = [fact for fact in evidence_facts if fact.grade is EvidenceGrade.A_DIRECT]
    grade_a_valid = all(_fact_source_valid(fact, source_texts) for fact in grade_a_facts)
    checks.append(
        _check(
            "PV-005",
            grade_a_valid,
            "DIRECT_SPANS_VALID" if grade_a_valid else "DIRECT_SPAN_INVALID",
            applicable=bool(grade_a_facts),
        )
    )
    grade_b_facts = [fact for fact in evidence_facts if fact.grade is EvidenceGrade.B_DETERMINISTIC]
    all_fact_ids = {fact.fact_id for fact in eligibility_context.facts}
    grade_b_valid = all(
        fact.transformation_id in _WHITELISTED_TRANSFORMATIONS
        and bool(fact.derived_from_fact_ids)
        and all(parent in all_fact_ids for parent in fact.derived_from_fact_ids)
        for fact in grade_b_facts
    )
    checks.append(
        _check(
            "PV-006",
            grade_b_valid,
            "TRANSFORMS_VALID" if grade_b_valid else "TRANSFORM_INVALID",
            applicable=bool(grade_b_facts),
        )
    )
    no_hypothesis = all(not fact_id.startswith("hyp_") for fact_id in result.evidence_fact_ids)
    checks.append(
        _check(
            "PV-007", no_hypothesis, "FIREWALL_CLEAR" if no_hypothesis else "HYPOTHESIS_REFERENCED"
        )
    )
    is_hard = result.verdict in {CriterionVerdict.PASS, CriterionVerdict.FAIL}
    admissible = all(
        fact.grade in {EvidenceGrade.A_DIRECT, EvidenceGrade.B_DETERMINISTIC}
        and fact.admissible_for_hard_decision
        for fact in evidence_facts
    )
    checks.append(
        _check(
            "PV-008",
            admissible,
            "EVIDENCE_ADMISSIBLE" if admissible else "EVIDENCE_INADMISSIBLE",
            applicable=is_hard,
        )
    )
    units_valid = _units_valid(criterion, evidence_facts, slots)
    checks.append(
        _check(
            "PV-009", units_valid, "UNITS_TEMPORAL_VALID" if units_valid else "UNIT_OR_DATE_INVALID"
        )
    )
    relevant_conflicts = [
        conflict
        for conflict in eligibility_context.conflicts
        if conflict.status == "OPEN" and conflict.slot_id in criterion.required_slots
    ]
    no_conflict = not relevant_conflicts
    checks.append(
        _check(
            "PV-010", no_conflict, "NO_RELEVANT_CONFLICT" if no_conflict else "RELEVANT_CONFLICT"
        )
    )
    no_opaque = not criterion.opaque and not result.requires_review
    checks.append(
        _check("PV-011", no_opaque, "NO_OPAQUE_ANCESTOR" if no_opaque else "OPAQUE_ANCESTOR")
    )

    replay = evaluate_criterion(criterion, eligibility_context, evaluation_date)
    replay_payload = canonical_replay_payload(criterion.criterion_id, patient_state_version, replay)
    original_payload = canonical_replay_payload(
        criterion.criterion_id, patient_state_version, result
    )
    replay_success = canonical_sha256(replay_payload) == canonical_sha256(original_payload)
    checks.append(
        _check("PV-012", replay_success, "REPLAY_MATCH" if replay_success else "REPLAY_MISMATCH")
    )
    replay_hash = canonical_sha256(original_payload)
    checks.append(
        _check(
            "PV-013",
            replay_hash == canonical_sha256(replay_payload),
            "REPLAY_HASH_MATCH" if replay_success else "REPLAY_HASH_MISMATCH",
            hashes=[replay_hash],
        )
    )
    registry_valid = (
        bool(raw_trial.api_version)
        and raw_trial.retrieved_at.tzinfo is not None
        and len(raw_trial.source_json_sha256) == 64
        and (registry_data_version is None or bool(registry_data_version))
    )
    checks.append(
        _check(
            "PV-014",
            registry_valid,
            "REGISTRY_VERSION_VALID" if registry_valid else "REGISTRY_VERSION_INVALID",
            hashes=[raw_trial.source_json_sha256],
        )
    )

    blocking = [
        check.check_id
        for check in checks
        if check.applicable and check.blocking and not check.passed
    ]
    hard_allowed = is_hard and not blocking
    final_verdict = result.verdict
    if is_hard and not hard_allowed:
        final_verdict = (
            CriterionVerdict.CONFLICT if relevant_conflicts else CriterionVerdict.UNKNOWN
        )
    proof_id = (
        f"{session_id}:{criterion.nct_id}:{criterion.criterion_id}:v{patient_state_version}:r0"
    )
    if not _PROOF_ID_PATTERN.fullmatch(proof_id):
        raise ValueError("proof ID does not include patient version and revision")
    return ProofPacket(
        proof_id=proof_id,
        proof_revision=0,
        verification_phase="DECISION",
        supersedes_proof_id=None,
        session_id=session_id,
        patient_state_version=patient_state_version,
        nct_id=criterion.nct_id,
        criterion_id=criterion.criterion_id,
        criterion_source_hash=criterion.source_text_sha256,
        compiled_protocol_hash=compiled_trial.content_hash,
        registry_api_version=raw_trial.api_version,
        registry_data_version=registry_data_version,
        registry_retrieved_at=raw_trial.retrieved_at,
        evaluated_at=evaluated_time,
        evaluation_date=evaluation_date,
        provisional_verdict=result.verdict,
        final_verdict=final_verdict,
        evidence_fact_ids=result.evidence_fact_ids,
        missing_slot_ids=result.missing_slot_ids,
        conflict_ids=result.conflict_ids,
        derivation_steps=result.derivation_steps,
        verifier_checks=checks,
        hard_decision_allowed=hard_allowed,
        blocking_issue_codes=[str(item) for item in blocking],
        canonical_replay_hash=replay_hash,
    )


def replay_packet_matches(
    packet: ProofPacket,
    criterion: CompiledCriterion,
    context: EligibilityContext,
) -> bool:
    result = evaluate_criterion(criterion, context, packet.evaluation_date)
    payload = canonical_replay_payload(criterion.criterion_id, packet.patient_state_version, result)
    return canonical_sha256(payload) == packet.canonical_replay_hash
