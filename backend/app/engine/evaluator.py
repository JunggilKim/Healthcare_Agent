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
        expected = _normalized_scalar(node.value)
        return (CriterionVerdict.PASS if patient_value == expected else CriterionVerdict.FAIL), None
    if node.op is AstOperator.IN:
        expected_values = {_normalized_scalar(value) for value in node.values}
        return (
            CriterionVerdict.PASS if patient_value in expected_values else CriterionVerdict.FAIL
        ), None
    if node.op in {AstOperator.GTE, AstOperator.GT, AstOperator.LTE, AstOperator.LT}:
        if not isinstance(fact.value, NumberValue) or not isinstance(node.value, NumberValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        if fact.value.unit != node.value.unit:
            return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
        comparisons = {
            AstOperator.GTE: fact.value.value >= node.value.value,
            AstOperator.GT: fact.value.value > node.value.value,
            AstOperator.LTE: fact.value.value <= node.value.value,
            AstOperator.LT: fact.value.value < node.value.value,
        }
        return (CriterionVerdict.PASS if comparisons[node.op] else CriterionVerdict.FAIL), None
    if node.op is AstOperator.BETWEEN_INCLUSIVE:
        if not isinstance(fact.value, NumberValue) or not isinstance(node.value, RangeValue):
            return CriterionVerdict.UNKNOWN, "TYPE_MISMATCH"
        if fact.value.unit != node.value.unit:
            return CriterionVerdict.UNKNOWN, "UNIT_CONVERSION_UNSUPPORTED"
        assert node.value.lower is not None and node.value.upper is not None
        passed = node.value.lower <= fact.value.value <= node.value.upper
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

    del (
        evaluation_date
    )  # Phase-1 operators have no temporal reference; kept in the public boundary.
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
