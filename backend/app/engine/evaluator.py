from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.app.domain.ast import AstNode, AstOperator
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, PatientFact
from backend.app.domain.proof import DerivationStep
from backend.app.domain.trials import CompiledCriterion
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    RangeValue,
    StringValue,
    TypedValue,
)
from backend.app.engine.temporal import directional_days
from backend.app.engine.unit_converter import UnitConversionError, default_unit_converter

EVALUATOR_VERSION = "evaluator-v1"


@dataclass(slots=True)
class EvaluationResult:
    verdict: CriterionVerdict
    evidence_fact_ids: list[str] = field(default_factory=list)
    missing_slot_ids: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    derivation_steps: list[DerivationStep] = field(default_factory=list)
    requires_review: bool = False
    issue_codes: list[str] = field(default_factory=list)


def _normalized_scalar(value: TypedValue) -> object:
    if isinstance(value, BooleanValue):
        return value.value
    if isinstance(value, NumberValue):
        return value.value
    if isinstance(value, StringValue):
        return (value.normalized or value.value).strip().casefold()
    if isinstance(value, CategoricalValue):
        return value.value.strip().casefold()
    if isinstance(value, DateValue):
        return value.value
    if isinstance(value, DurationValue):
        return value.days
    if isinstance(value, RangeValue):
        return (value.lower, value.upper, value.lower_inclusive, value.upper_inclusive)
    return None


def _json_scalar(value: object) -> str | int | bool | None:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _step(
    criterion_id: str,
    node: AstNode,
    index: int,
    operation: str,
    fact_ids: list[str],
    input_step_ids: list[str],
    verdict: CriterionVerdict,
    parameters: dict[str, str | int | bool | None] | None = None,
) -> DerivationStep:
    return DerivationStep(
        step_id=f"{criterion_id}:step:{index}",
        operation=operation,
        input_fact_ids=sorted(set(fact_ids)),
        input_step_ids=input_step_ids,
        parameters={"node_id": node.node_id, **(parameters or {})},
        output={"verdict": verdict.value},
        code_version=EVALUATOR_VERSION,
    )


def _applicable_facts(slot_id: str, context: EligibilityContext) -> list[PatientFact]:
    return [
        fact
        for fact in context.facts
        if fact.slot_id == slot_id
        and fact.grade in {EvidenceGrade.A_DIRECT, EvidenceGrade.B_DETERMINISTIC}
        and fact.admissible_for_hard_decision
    ]


def _open_conflicts(slot_id: str, context: EligibilityContext) -> list[str]:
    return sorted(
        conflict.conflict_id
        for conflict in context.conflicts
        if conflict.slot_id == slot_id and conflict.status == "OPEN"
    )


def _compare_fact(node: AstNode, fact: PatientFact) -> tuple[CriterionVerdict, str | None]:
    patient_value = _normalized_scalar(fact.value)
    if node.op is AstOperator.EXISTS:
        return CriterionVerdict.PASS, None
    if node.op is AstOperator.EQ:
        assert node.value is not None
        if isinstance(fact.value, NumberValue) and isinstance(node.value, NumberValue):
            number = fact.value.value
            if fact.value.unit != node.value.unit:
                if fact.value.unit is None or node.value.unit is None:
                    return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
                try:
                    number = default_unit_converter().convert(
                        number, fact.value.unit, node.value.unit
                    )
                except UnitConversionError:
                    return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
            return (
                CriterionVerdict.PASS if number == node.value.value else CriterionVerdict.FAIL
            ), None
        expected = _normalized_scalar(node.value)
        return (CriterionVerdict.PASS if patient_value == expected else CriterionVerdict.FAIL), None
    if node.op is AstOperator.IN:
        if isinstance(fact.value, NumberValue) and all(
            isinstance(value, NumberValue) for value in node.values
        ):
            outcomes: list[bool] = []
            for value in node.values:
                assert isinstance(value, NumberValue)
                number = fact.value.value
                if fact.value.unit != value.unit:
                    if fact.value.unit is None or value.unit is None:
                        return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
                    try:
                        number = default_unit_converter().convert(
                            number, fact.value.unit, value.unit
                        )
                    except UnitConversionError:
                        return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
                outcomes.append(number == value.value)
            return (CriterionVerdict.PASS if any(outcomes) else CriterionVerdict.FAIL), None
        expected_values = {_normalized_scalar(value) for value in node.values}
        return (
            CriterionVerdict.PASS if patient_value in expected_values else CriterionVerdict.FAIL
        ), None
    if node.op in {AstOperator.GTE, AstOperator.GT, AstOperator.LTE, AstOperator.LT}:
        if not isinstance(fact.value, NumberValue) or not isinstance(node.value, NumberValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        patient_number = fact.value.value
        if fact.value.unit != node.value.unit:
            if fact.value.unit is None or node.value.unit is None:
                return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
            try:
                patient_number = default_unit_converter().convert(
                    patient_number, fact.value.unit, node.value.unit
                )
            except UnitConversionError:
                return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
        threshold = node.value.value
        comparisons = {
            AstOperator.GTE: patient_number >= threshold,
            AstOperator.GT: patient_number > threshold,
            AstOperator.LTE: patient_number <= threshold,
            AstOperator.LT: patient_number < threshold,
        }
        return (CriterionVerdict.PASS if comparisons[node.op] else CriterionVerdict.FAIL), None
    if node.op is AstOperator.BETWEEN_INCLUSIVE:
        if not isinstance(fact.value, NumberValue) or not isinstance(node.value, RangeValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        patient_number = fact.value.value
        if fact.value.unit != node.value.unit:
            if fact.value.unit is None or node.value.unit is None:
                return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
            try:
                patient_number = default_unit_converter().convert(
                    patient_number, fact.value.unit, node.value.unit
                )
            except UnitConversionError:
                return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
        assert node.value.lower is not None and node.value.upper is not None
        passed = node.value.lower <= patient_number <= node.value.upper
        return (CriterionVerdict.PASS if passed else CriterionVerdict.FAIL), None
    if node.op is AstOperator.DURATION_AT_LEAST_DAYS:
        if not isinstance(fact.value, DurationValue) or not isinstance(node.value, DurationValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        return (
            CriterionVerdict.PASS if fact.value.days >= node.value.days else CriterionVerdict.FAIL
        ), None
    if node.op in {AstOperator.BEFORE, AstOperator.AFTER} and isinstance(node.value, DateValue):
        if not isinstance(fact.value, DateValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        passed = (
            fact.value.value < node.value.value
            if node.op is AstOperator.BEFORE
            else fact.value.value > node.value.value
        )
        return (CriterionVerdict.PASS if passed else CriterionVerdict.FAIL), None
    return CriterionVerdict.UNKNOWN, "OPERATOR_REQUIRES_CONTEXT"


def _aggregate_all(children: list[EvaluationResult]) -> CriterionVerdict:
    verdicts = [child.verdict for child in children]
    if CriterionVerdict.FAIL in verdicts:
        return CriterionVerdict.FAIL
    if CriterionVerdict.CONFLICT in verdicts:
        return CriterionVerdict.CONFLICT
    if CriterionVerdict.UNKNOWN in verdicts:
        return CriterionVerdict.UNKNOWN
    if CriterionVerdict.PASS in verdicts:
        return CriterionVerdict.PASS
    return CriterionVerdict.NOT_APPLICABLE


def _aggregate_any(children: list[EvaluationResult]) -> CriterionVerdict:
    verdicts = [child.verdict for child in children]
    if CriterionVerdict.PASS in verdicts:
        return CriterionVerdict.PASS
    if CriterionVerdict.CONFLICT in verdicts:
        return CriterionVerdict.CONFLICT
    if CriterionVerdict.UNKNOWN in verdicts:
        return CriterionVerdict.UNKNOWN
    if any(verdict is CriterionVerdict.FAIL for verdict in verdicts):
        return CriterionVerdict.FAIL
    return CriterionVerdict.NOT_APPLICABLE


def _merge_children(
    verdict: CriterionVerdict, children: list[EvaluationResult]
) -> EvaluationResult:
    return EvaluationResult(
        verdict=verdict,
        evidence_fact_ids=sorted({item for child in children for item in child.evidence_fact_ids}),
        missing_slot_ids=sorted({item for child in children for item in child.missing_slot_ids}),
        conflict_ids=sorted({item for child in children for item in child.conflict_ids}),
        derivation_steps=[step for child in children for step in child.derivation_steps],
        requires_review=any(child.requires_review for child in children),
        issue_codes=sorted({item for child in children for item in child.issue_codes}),
    )


def evaluate_criterion(
    criterion: CompiledCriterion,
    context: EligibilityContext,
    evaluation_date: date,
) -> EvaluationResult:
    """Evaluate one verified bounded AST with open-world semantics."""

    nodes = {node.node_id: node for node in criterion.ast.nodes}
    step_index = 0

    def evaluate_node(node_id: str) -> EvaluationResult:
        nonlocal step_index
        node = nodes[node_id]
        child_results = [evaluate_node(child_id) for child_id in node.child_ids]
        if node.op in {AstOperator.ALL, AstOperator.ANY, AstOperator.NOT, AstOperator.IMPLIES}:
            if node.op is AstOperator.ALL:
                verdict = _aggregate_all(child_results)
                operation = "AGGREGATE_ALL"
            elif node.op is AstOperator.ANY:
                verdict = _aggregate_any(child_results)
                operation = "AGGREGATE_ANY"
            elif node.op is AstOperator.NOT:
                inverted = {
                    CriterionVerdict.PASS: CriterionVerdict.FAIL,
                    CriterionVerdict.FAIL: CriterionVerdict.PASS,
                }
                verdict = inverted.get(child_results[0].verdict, child_results[0].verdict)
                operation = "APPLY_NOT"
            else:
                antecedent, consequent = child_results
                if antecedent.verdict is CriterionVerdict.FAIL:
                    verdict = CriterionVerdict.NOT_APPLICABLE
                elif antecedent.verdict is CriterionVerdict.PASS:
                    verdict = consequent.verdict
                else:
                    verdict = antecedent.verdict
                operation = "APPLY_IMPLIES"
            result = _merge_children(verdict, child_results)
            step_index += 1
            result.derivation_steps.append(
                _step(
                    criterion.criterion_id,
                    node,
                    step_index,
                    operation,
                    result.evidence_fact_ids,
                    [child.derivation_steps[-1].step_id for child in child_results],
                    verdict,
                )
            )
            return result

        if node.op is AstOperator.OPAQUE:
            step_index += 1
            return EvaluationResult(
                verdict=CriterionVerdict.UNKNOWN,
                requires_review=True,
                issue_codes=["OPAQUE_CRITERION"],
                derivation_steps=[
                    _step(
                        criterion.criterion_id,
                        node,
                        step_index,
                        "OPAQUE_REQUIRES_REVIEW",
                        [],
                        [],
                        CriterionVerdict.UNKNOWN,
                    )
                ],
            )

        assert node.slot_id is not None
        conflicts = _open_conflicts(node.slot_id, context)
        if conflicts:
            verdict = CriterionVerdict.CONFLICT
            step_index += 1
            return EvaluationResult(
                verdict=verdict,
                conflict_ids=conflicts,
                derivation_steps=[
                    _step(
                        criterion.criterion_id,
                        node,
                        step_index,
                        "DETECT_CONFLICT",
                        [],
                        [],
                        verdict,
                    )
                ],
            )
        facts = _applicable_facts(node.slot_id, context)
        if not facts:
            verdict = CriterionVerdict.UNKNOWN
            step_index += 1
            return EvaluationResult(
                verdict=verdict,
                missing_slot_ids=[node.slot_id],
                derivation_steps=[
                    _step(
                        criterion.criterion_id,
                        node,
                        step_index,
                        "MISSING_ADMISSIBLE_FACT",
                        [],
                        [],
                        verdict,
                    )
                ],
            )

        if node.op is AstOperator.WITHIN_DAYS or (
            node.op in {AstOperator.BEFORE, AstOperator.AFTER}
            and node.metadata.get("reference_kind") == "SLOT"
        ):
            reference_kind = node.metadata.get("reference_kind")
            reference_facts: list[PatientFact] = []
            if reference_kind == "SLOT":
                reference_slot_id = node.metadata.get("reference_slot_id")
                assert isinstance(reference_slot_id, str)
                reference_conflicts = _open_conflicts(reference_slot_id, context)
                if reference_conflicts:
                    step_index += 1
                    return EvaluationResult(
                        verdict=CriterionVerdict.CONFLICT,
                        conflict_ids=reference_conflicts,
                        derivation_steps=[
                            _step(
                                criterion.criterion_id,
                                node,
                                step_index,
                                "DETECT_REFERENCE_CONFLICT",
                                [],
                                [],
                                CriterionVerdict.CONFLICT,
                            )
                        ],
                    )
                reference_facts = _applicable_facts(reference_slot_id, context)
                if not reference_facts:
                    step_index += 1
                    return EvaluationResult(
                        verdict=CriterionVerdict.UNKNOWN,
                        missing_slot_ids=[reference_slot_id],
                        derivation_steps=[
                            _step(
                                criterion.criterion_id,
                                node,
                                step_index,
                                "MISSING_REFERENCE_DATE",
                                [],
                                [],
                                CriterionVerdict.UNKNOWN,
                            )
                        ],
                    )

            temporal_results: list[CriterionVerdict] = []
            issues: list[str] = []
            for event_fact in facts:
                if not isinstance(event_fact.value, DateValue):
                    issues.append("TYPE_MISMATCH")
                    continue
                reference_dates: list[date] = []
                if reference_kind == "SLOT":
                    for reference_fact in reference_facts:
                        if isinstance(reference_fact.value, DateValue):
                            reference_dates.append(reference_fact.value.value)
                        else:
                            issues.append("TYPE_MISMATCH")
                else:
                    reference_dates.append(evaluation_date)
                for reference_date in reference_dates:
                    if node.op is AstOperator.WITHIN_DAYS:
                        assert isinstance(node.value, DurationValue)
                        days = directional_days(
                            event_fact.value.value,
                            reference_date,
                            str(node.metadata["direction"]),
                        )
                        passed = days is not None and days <= node.value.days
                    else:
                        inclusive = bool(node.metadata["inclusive"])
                        if node.op is AstOperator.BEFORE:
                            passed = (
                                event_fact.value.value <= reference_date
                                if inclusive
                                else event_fact.value.value < reference_date
                            )
                        else:
                            passed = (
                                event_fact.value.value >= reference_date
                                if inclusive
                                else event_fact.value.value > reference_date
                            )
                    temporal_results.append(
                        CriterionVerdict.PASS if passed else CriterionVerdict.FAIL
                    )
            if issues or not temporal_results:
                verdict = CriterionVerdict.UNKNOWN
            elif len(set(temporal_results)) > 1:
                verdict = CriterionVerdict.CONFLICT
                issues.append("MULTIPLE_INCOMPATIBLE_VALUES")
            else:
                verdict = temporal_results[0]
            evidence_ids = [fact.fact_id for fact in facts + reference_facts]
            step_index += 1
            return EvaluationResult(
                verdict=verdict,
                evidence_fact_ids=evidence_ids,
                missing_slot_ids=[node.slot_id] if verdict is CriterionVerdict.UNKNOWN else [],
                issue_codes=sorted(set(issues)),
                derivation_steps=[
                    _step(
                        criterion.criterion_id,
                        node,
                        step_index,
                        f"COMPARE_{node.op.value}",
                        evidence_ids,
                        [],
                        verdict,
                    )
                ],
            )

        comparisons = [_compare_fact(node, fact) for fact in facts]
        verdicts = {item[0] for item in comparisons}
        issues = sorted({item[1] for item in comparisons if item[1] is not None})
        if issues:
            verdict = CriterionVerdict.UNKNOWN
        elif len(verdicts) > 1:
            verdict = CriterionVerdict.CONFLICT
            issues = ["MULTIPLE_INCOMPATIBLE_VALUES"]
        else:
            verdict = next(iter(verdicts))
        step_index += 1
        threshold = None if node.value is None else _json_scalar(_normalized_scalar(node.value))
        return EvaluationResult(
            verdict=verdict,
            evidence_fact_ids=[fact.fact_id for fact in facts],
            missing_slot_ids=[node.slot_id] if verdict is CriterionVerdict.UNKNOWN else [],
            issue_codes=issues,
            derivation_steps=[
                _step(
                    criterion.criterion_id,
                    node,
                    step_index,
                    f"COMPARE_{node.op.value}",
                    [fact.fact_id for fact in facts],
                    [],
                    verdict,
                    {"threshold": threshold},
                )
            ],
        )

    return evaluate_node(criterion.ast.root_node_id)
