from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, FactConflict, PatientFact, SourceSpan
from backend.app.domain.trials import CompiledTrial
from backend.app.engine.evaluator import EvaluationResult, evaluate_criterion
from backend.app.evaluation.annotations import (
    AdjudicatedAnnotation,
    AnnotationAssignment,
    AnnotationVerdict,
)
from backend.app.evaluation.metrics import classification_metrics, mean
from backend.app.evaluation.models import PatientWorld, WorldFact

_MATCHING_LABELS = [item.value for item in CriterionVerdict]


def eligibility_context_from_world(
    facts: list[WorldFact],
    conflict_slots: list[str],
    *,
    evaluation_date: date,
    language: str,
) -> EligibilityContext:
    patient_facts: list[PatientFact] = []
    for fact in facts:
        quote = canonical_json_bytes(
            {"slot_id": fact.slot_id, "value": fact.value.model_dump(mode="json")}
        ).decode()
        patient_facts.append(
            PatientFact(
                fact_id=fact.fact_id,
                slot_id=fact.slot_id,
                value=fact.value,
                grade=EvidenceGrade.A_DIRECT,
                source_spans=[
                    SourceSpan(
                        source_id=f"benchmark:{fact.fact_id}",
                        start=0,
                        end=len(quote),
                        quote=quote,
                        sha256=hashlib.sha256(quote.encode()).hexdigest(),
                        language=language if language in {"en", "ko"} else "other",
                    )
                ],
                asserted_at=datetime.combine(evaluation_date, datetime.min.time(), tzinfo=UTC),
                effective_date=evaluation_date,
                admissible_for_hard_decision=True,
            )
        )
    conflicts: list[FactConflict] = []
    for slot_id in sorted(set(conflict_slots)):
        fact_ids = [fact.fact_id for fact in patient_facts if fact.slot_id == slot_id]
        if len(fact_ids) < 2:
            raise ValueError(f"BENCHMARK_CONFLICT_REQUIRES_TWO_FACTS:{slot_id}")
        conflicts.append(
            FactConflict(
                conflict_id=f"conflict_{hashlib.sha256(slot_id.encode()).hexdigest()[:16]}",
                slot_id=slot_id,
                fact_ids=fact_ids,
                conflict_type="VALUE_MISMATCH",
                status="OPEN",
            )
        )
    return EligibilityContext(facts=patient_facts, conflicts=conflicts)


def benchmark_fact_source_texts(facts: list[WorldFact]) -> dict[str, str]:
    """Return the exact source strings used by ``eligibility_context_from_world``."""

    return {
        f"benchmark:{fact.fact_id}": canonical_json_bytes(
            {"slot_id": fact.slot_id, "value": fact.value.model_dump(mode="json")}
        ).decode()
        for fact in facts
    }


def evaluate_world(trial: CompiledTrial, world: PatientWorld) -> dict[str, EvaluationResult]:
    if trial.nct_id != world.nct_id or trial.content_hash != world.compiled_protocol_hash:
        raise ValueError(f"BENCHMARK_WORLD_PROTOCOL_MISMATCH:{world.world_id}")
    context = eligibility_context_from_world(
        world.facts,
        world.conflict_slots,
        evaluation_date=world.evaluation_date,
        language=world.narrative_language,
    )
    return {
        criterion.criterion_id: evaluate_criterion(criterion, context, world.evaluation_date)
        for criterion in trial.criteria
    }


def evaluate_adjudicated_subset(
    assignments: list[AnnotationAssignment],
    gold: list[AdjudicatedAnnotation],
    compiled_trials: list[CompiledTrial],
) -> dict[str, Any]:
    assignment_by_id = {item.record_id: item for item in assignments}
    if len(assignment_by_id) != len(assignments):
        raise ValueError("EVALUATION_DUPLICATE_ASSIGNMENT")
    trials = {item.nct_id: item for item in compiled_trials}
    criteria = {
        criterion.criterion_id: criterion
        for trial in compiled_trials
        for criterion in trial.criteria
    }
    truth: list[str] = []
    predictions: list[str] = []
    rows: list[dict[str, Any]] = []
    evidence_correct = 0
    evidence_predicted = 0
    evidence_expected = 0
    evidence_recovered = 0
    excluded = 0
    for annotation in gold:
        assignment = assignment_by_id.get(annotation.record_id)
        if assignment is None or annotation.assignment_hash != assignment.assignment_hash:
            raise ValueError(f"EVALUATION_GOLD_ASSIGNMENT_MISMATCH:{annotation.record_id}")
        trial = trials.get(assignment.nct_id)
        criterion = criteria.get(assignment.criterion_id)
        if trial is None or criterion is None or criterion.nct_id != assignment.nct_id:
            raise ValueError(f"EVALUATION_COMPILED_CRITERION_MISSING:{annotation.record_id}")
        if trial.content_hash != assignment.compiled_protocol_hash:
            raise ValueError(f"EVALUATION_PROTOCOL_HASH_MISMATCH:{annotation.record_id}")
        context = eligibility_context_from_world(
            assignment.facts,
            assignment.conflict_slots,
            evaluation_date=assignment.evaluation_date,
            language=assignment.narrative_language,
        )
        result = evaluate_criterion(criterion, context, assignment.evaluation_date)
        included = (
            annotation.safely_executable
            and annotation.verdict is not AnnotationVerdict.OPAQUE
            and annotation.verdict.value in _MATCHING_LABELS
        )
        if included:
            truth.append(annotation.verdict.value)
            predictions.append(result.verdict.value)
            expected_ids = set(annotation.evidence_fact_ids)
            predicted_ids = set(result.evidence_fact_ids)
            evidence_correct += len(expected_ids & predicted_ids)
            evidence_predicted += len(predicted_ids)
            evidence_expected += len(expected_ids)
            evidence_recovered += len(expected_ids & predicted_ids)
        else:
            excluded += 1
        rows.append(
            {
                "record_id": annotation.record_id,
                "world_id": assignment.world_id,
                "nct_id": assignment.nct_id,
                "criterion_id": assignment.criterion_id,
                "criticality": assignment.criticality,
                "split": assignment.split,
                "included_in_matching_metrics": included,
                "gold_verdict": annotation.verdict.value,
                "system_verdict": result.verdict.value,
                "gold_evidence_fact_ids": sorted(annotation.evidence_fact_ids),
                "system_evidence_fact_ids": sorted(result.evidence_fact_ids),
                "system_missing_slot_ids": sorted(result.missing_slot_ids),
                "system_requires_review": result.requires_review,
                "explanation_supported_by_reviewer": annotation.explanation_supported,
            }
        )
    metrics = classification_metrics(truth, predictions, _MATCHING_LABELS)
    fail_metrics = metrics["per_class"][CriterionVerdict.FAIL.value]
    included_rows = [row for row in rows if row["included_in_matching_metrics"]]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["world_id"]), str(row["nct_id"])), []).append(row)
    complete_groups: list[list[dict[str, Any]]] = []
    incomplete_groups: list[str] = []
    for (world_id, nct_id), group_rows in sorted(grouped.items()):
        expected = {
            criterion.criterion_id for criterion in trials[nct_id].criteria if not criterion.opaque
        }
        actual = {str(row["criterion_id"]) for row in group_rows}
        if actual != expected:
            incomplete_groups.append(f"{world_id}:{nct_id}")
        else:
            complete_groups.append(group_rows)

    def trial_decision(group_rows: list[dict[str, Any]], label_key: str) -> str:
        nct_id = str(group_rows[0]["nct_id"])
        if any(
            criterion.criticality == "CRITICAL" and criterion.opaque
            for criterion in trials[nct_id].criteria
        ):
            return "UNRESOLVED"
        critical = [row for row in group_rows if row["criticality"] == "CRITICAL"]
        labels = {str(row[label_key]) for row in critical}
        if CriterionVerdict.FAIL.value in labels:
            return "INELIGIBLE"
        if labels & {
            CriterionVerdict.UNKNOWN.value,
            CriterionVerdict.CONFLICT.value,
            AnnotationVerdict.OPAQUE.value,
        }:
            return "UNRESOLVED"
        return "PRE_SCREEN_PASS"

    false_pre_screen_passes = sum(
        trial_decision(group_rows, "system_verdict") == "PRE_SCREEN_PASS"
        and trial_decision(group_rows, "gold_verdict") != "PRE_SCREEN_PASS"
        for group_rows in complete_groups
    )
    return {
        "scope": "DATASET_A_MANUALLY_ADJUDICATED_CRITERION_SUBSET",
        "acceptance_eligible": len(included_rows) >= 200 and not incomplete_groups,
        "reviewed_count": len(gold),
        "matching_count": len(included_rows),
        "excluded_opaque_or_unsafe": excluded,
        "criterion_metrics": metrics,
        "hard_fail_recall": float(fail_metrics["recall"]),
        "complete_trial_world_groups": len(complete_groups),
        "incomplete_trial_world_groups": incomplete_groups,
        "false_pre_screen_pass_rate": false_pre_screen_passes / len(complete_groups)
        if complete_groups
        else 1.0,
        "evidence_precision": evidence_correct / evidence_predicted
        if evidence_predicted
        else float(evidence_expected == 0),
        "evidence_recall": evidence_recovered / evidence_expected if evidence_expected else 1.0,
        "explanation_support_rate": mean(
            [float(row["explanation_supported_by_reviewer"]) for row in included_rows]
        ),
        "predictions": rows,
    }
