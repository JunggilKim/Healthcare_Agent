from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backend.app.domain.ast import AstOperator, CriterionAst
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, PatientFact, SourceSpan
from backend.app.domain.trials import CompiledCriterion
from backend.app.domain.values import (
    BooleanValue,
    DateValue,
    DurationValue,
    NumberValue,
    RangeValue,
    TypedValue,
)
from backend.app.engine.evaluator import evaluate_criterion


@dataclass(frozen=True)
class BoundaryCase:
    case_id: str
    node_id: str
    label: str
    facts: list[PatientFact]
    expected: CriterionVerdict


@dataclass(frozen=True)
class BoundaryReport:
    cases: list[BoundaryCase]
    passed: bool
    failures: list[str]


def _fact(slot_id: str, value: TypedValue, case_id: str) -> PatientFact:
    quote = case_id
    return PatientFact(
        fact_id=f"fact_boundary_{case_id}",
        slot_id=slot_id,
        value=value,
        grade=EvidenceGrade.A_DIRECT,
        source_spans=[
            SourceSpan(
                source_id="generated-boundary-test",
                start=0,
                end=len(quote),
                quote=quote,
                sha256=hashlib.sha256(quote.encode()).hexdigest(),
                language="en",
            )
        ],
        asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
        admissible_for_hard_decision=True,
    )


def _precision_step(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("boundary values must be finite decimals")
    return Decimal(1).scaleb(exponent)


def _append_case(
    cases: list[BoundaryCase],
    *,
    slot_id: str,
    node_id: str,
    case_prefix: str,
    label: str,
    value: TypedValue | None,
    expected: CriterionVerdict,
) -> None:
    facts = [] if value is None else [_fact(slot_id, value, f"{case_prefix}:{label}")]
    cases.append(
        BoundaryCase(
            case_id=f"{case_prefix}:{label}",
            node_id=node_id,
            label=label,
            facts=facts,
            expected=expected,
        )
    )


def generate_boundary_cases(
    criterion: CompiledCriterion, evaluation_date: date
) -> list[BoundaryCase]:
    cases: list[BoundaryCase] = []
    for node in criterion.ast.nodes:
        if node.slot_id is None:
            continue
        case_prefix = f"{criterion.criterion_id}:{node.node_id.rsplit(':', 1)[-1]}"

        def add(
            label: str,
            value: TypedValue | None,
            expected: CriterionVerdict,
            *,
            slot_id: str = node.slot_id or "",
            node_id: str = node.node_id,
            prefix: str = case_prefix,
        ) -> None:
            _append_case(
                cases,
                slot_id=slot_id,
                node_id=node_id,
                case_prefix=prefix,
                label=label,
                value=value,
                expected=expected,
            )

        if node.op in {AstOperator.GTE, AstOperator.GT, AstOperator.LTE, AstOperator.LT}:
            assert isinstance(node.value, NumberValue)
            step = _precision_step(node.value.value)
            expected = {
                AstOperator.GTE: (
                    CriterionVerdict.FAIL,
                    CriterionVerdict.PASS,
                    CriterionVerdict.PASS,
                ),
                AstOperator.GT: (
                    CriterionVerdict.FAIL,
                    CriterionVerdict.FAIL,
                    CriterionVerdict.PASS,
                ),
                AstOperator.LTE: (
                    CriterionVerdict.PASS,
                    CriterionVerdict.PASS,
                    CriterionVerdict.FAIL,
                ),
                AstOperator.LT: (
                    CriterionVerdict.PASS,
                    CriterionVerdict.FAIL,
                    CriterionVerdict.FAIL,
                ),
            }[node.op]
            for label, number, verdict in zip(
                ("below", "exact", "above"),
                (node.value.value - step, node.value.value, node.value.value + step),
                expected,
                strict=True,
            ):
                add(
                    label,
                    NumberValue(kind="number", value=number, unit=node.value.unit),
                    verdict,
                )
            incompatible = "kg" if node.value.unit != "kg" else "day"
            add(
                "incompatible_unit",
                NumberValue(kind="number", value=node.value.value, unit=incompatible),
                CriterionVerdict.UNKNOWN,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif node.op is AstOperator.BETWEEN_INCLUSIVE:
            assert isinstance(node.value, RangeValue)
            assert node.value.lower is not None and node.value.upper is not None
            lower_step = _precision_step(node.value.lower)
            upper_step = _precision_step(node.value.upper)
            for label, number, verdict in (
                ("below", node.value.lower - lower_step, CriterionVerdict.FAIL),
                ("lower_exact", node.value.lower, CriterionVerdict.PASS),
                ("upper_exact", node.value.upper, CriterionVerdict.PASS),
                ("above", node.value.upper + upper_step, CriterionVerdict.FAIL),
            ):
                add(
                    label,
                    NumberValue(kind="number", value=number, unit=node.value.unit),
                    verdict,
                )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif node.op is AstOperator.EQ and isinstance(node.value, NumberValue):
            step = _precision_step(node.value.value)
            add(
                "below",
                NumberValue(kind="number", value=node.value.value - step, unit=node.value.unit),
                CriterionVerdict.FAIL,
            )
            add(
                "exact",
                NumberValue(kind="number", value=node.value.value, unit=node.value.unit),
                CriterionVerdict.PASS,
            )
            add(
                "above",
                NumberValue(kind="number", value=node.value.value + step, unit=node.value.unit),
                CriterionVerdict.FAIL,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif (
            node.op is AstOperator.IN
            and node.values
            and all(isinstance(value, NumberValue) for value in node.values)
        ):
            numeric_values = [value for value in node.values if isinstance(value, NumberValue)]
            for index, value in enumerate(numeric_values):
                add(
                    f"member_{index}",
                    NumberValue(kind="number", value=value.value, unit=value.unit),
                    CriterionVerdict.PASS,
                )
            minimum = min(value.value for value in numeric_values)
            maximum = max(value.value for value in numeric_values)
            unit = numeric_values[0].unit
            add(
                "below",
                NumberValue(kind="number", value=minimum - _precision_step(minimum), unit=unit),
                CriterionVerdict.FAIL,
            )
            add(
                "above",
                NumberValue(kind="number", value=maximum + _precision_step(maximum), unit=unit),
                CriterionVerdict.FAIL,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif node.op is AstOperator.DURATION_AT_LEAST_DAYS:
            assert isinstance(node.value, DurationValue)
            if node.value.days > 0:
                add(
                    "below",
                    DurationValue(kind="duration", days=node.value.days - 1),
                    CriterionVerdict.FAIL,
                )
            add(
                "exact",
                DurationValue(kind="duration", days=node.value.days),
                CriterionVerdict.PASS,
            )
            add(
                "above",
                DurationValue(kind="duration", days=node.value.days + 1),
                CriterionVerdict.PASS,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif node.op in {AstOperator.BEFORE, AstOperator.AFTER} and isinstance(
            node.value, DateValue
        ):
            before_expected = (
                CriterionVerdict.PASS if node.op is AstOperator.BEFORE else CriterionVerdict.FAIL
            )
            after_expected = (
                CriterionVerdict.FAIL if node.op is AstOperator.BEFORE else CriterionVerdict.PASS
            )
            add(
                "before",
                DateValue(kind="date", value=node.value.value - timedelta(days=1), precision="DAY"),
                before_expected,
            )
            add(
                "exact",
                DateValue(kind="date", value=node.value.value, precision="DAY"),
                CriterionVerdict.FAIL,
            )
            add(
                "after",
                DateValue(kind="date", value=node.value.value + timedelta(days=1), precision="DAY"),
                after_expected,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif (
            node.op is AstOperator.WITHIN_DAYS
            and node.metadata.get("reference_kind") == "EVALUATION_DATE"
        ):
            assert isinstance(node.value, DurationValue)
            add(
                "exact",
                DateValue(
                    kind="date",
                    value=evaluation_date - timedelta(days=node.value.days),
                    precision="DAY",
                ),
                CriterionVerdict.PASS,
            )
            add(
                "outside",
                DateValue(
                    kind="date",
                    value=evaluation_date - timedelta(days=node.value.days + 1),
                    precision="DAY",
                ),
                CriterionVerdict.FAIL,
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
        elif node.op is AstOperator.EQ and isinstance(node.value, BooleanValue):
            add(
                "true",
                BooleanValue(kind="boolean", value=True),
                (CriterionVerdict.PASS if node.value.value else CriterionVerdict.FAIL),
            )
            add(
                "false",
                BooleanValue(kind="boolean", value=False),
                (CriterionVerdict.FAIL if node.value.value else CriterionVerdict.PASS),
            )
            add("unknown", None, CriterionVerdict.UNKNOWN)
    return cases


def run_boundary_tests(criterion: CompiledCriterion, evaluation_date: date) -> BoundaryReport:
    cases = generate_boundary_cases(criterion, evaluation_date)
    failures: list[str] = []
    nodes = {node.node_id: node for node in criterion.ast.nodes}
    for case in cases:
        leaf = nodes[case.node_id]
        leaf_criterion = criterion.model_copy(
            update={
                "ast": CriterionAst(root_node_id=leaf.node_id, nodes=[leaf]),
                "required_slots": [leaf.slot_id] if leaf.slot_id else [],
            }
        )
        actual = evaluate_criterion(
            leaf_criterion,
            EligibilityContext(facts=case.facts, conflicts=[]),
            evaluation_date,
        ).verdict
        if actual is not case.expected:
            failures.append(f"{case.case_id}: expected {case.expected.value}, found {actual.value}")
    return BoundaryReport(cases=cases, passed=not failures, failures=failures)
