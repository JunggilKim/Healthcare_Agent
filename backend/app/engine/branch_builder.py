from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import UUID

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.ast import AstNode, AstOperator
from backend.app.domain.questions import AnswerBranch
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


def deterministic_question_id(session_id: str, state_version: int, slot_id: str) -> str:
    payload = f"{session_id}:{state_version}:{slot_id}".encode()
    return f"q_{UUID(bytes=hashlib.sha256(payload).digest()[:16], version=4)}"


def _uniform_weight(count: int) -> float:
    return float(Decimal(1) / Decimal(count))


def _answer_branches(
    question_id: str,
    values: list[tuple[str, str, TypedValue | None]],
) -> list[AnswerBranch]:
    weight = _uniform_weight(len(values))
    return [
        AnswerBranch.model_validate(
            {
                "branch_id": f"{question_id}:{index}",
                "label": label,
                "response_kind": response_kind,
                "synthetic_value": value,
                "weight": weight,
            }
        )
        for index, (label, response_kind, value) in enumerate(values)
    ]


def _slot_nodes(criteria: list[CompiledCriterion], slot_id: str) -> list[AstNode]:
    return [
        node for criterion in criteria for node in criterion.ast.nodes if node.slot_id == slot_id
    ]


def _categorical_values(nodes: list[AstNode]) -> list[CategoricalValue | StringValue]:
    result: list[CategoricalValue | StringValue] = []
    seen: set[str] = set()
    for node in nodes:
        values: list[TypedValue] = [*node.values]
        if node.value is not None:
            values.append(node.value)
        for value in values:
            if isinstance(value, CategoricalValue):
                key = value.value.strip().casefold()
            elif isinstance(value, StringValue):
                key = (value.normalized or value.value).strip().casefold()
            else:
                continue
            if key not in seen:
                seen.add(key)
                result.append(value)
    return result[:4]


def _numeric_thresholds(nodes: list[AstNode]) -> list[NumberValue]:
    by_value: dict[Decimal, NumberValue] = {}
    for node in nodes:
        values: list[TypedValue] = [*node.values]
        if node.value is not None:
            values.append(node.value)
        for value in values:
            if isinstance(value, NumberValue):
                by_value.setdefault(value.value, value)
            elif isinstance(value, RangeValue):
                lower = value.lower
                upper = value.upper
                unit = value.unit
                if isinstance(lower, Decimal):
                    by_value.setdefault(lower, NumberValue(kind="number", value=lower, unit=unit))
                if isinstance(upper, Decimal):
                    by_value.setdefault(upper, NumberValue(kind="number", value=upper, unit=unit))
    return [by_value[value] for value in sorted(by_value)]


def _numeric_representatives(
    thresholds: list[NumberValue], slot: SlotDefinition, limit: int
) -> list[NumberValue]:
    unit = (
        thresholds[0].unit
        if thresholds
        else (slot.allowed_units[0] if slot.allowed_units else None)
    )
    if not thresholds:
        if slot.allowed_range:
            values = [Decimal(slot.allowed_range[0]), Decimal(slot.allowed_range[-1])]
        else:
            values = [Decimal(0), Decimal(1)]
        return [NumberValue(kind="number", value=value, unit=unit) for value in values[:limit]]

    numbers = [item.value for item in thresholds]
    smallest_gap = min(
        (right - left for left, right in pairwise(numbers)),
        default=Decimal(2),
    )
    delta = max(Decimal("0.001"), smallest_gap / Decimal(2))
    candidates: list[Decimal] = [numbers[0] - delta]
    for index, value in enumerate(numbers):
        candidates.append(value)
        if index + 1 < len(numbers):
            candidates.append((value + numbers[index + 1]) / Decimal(2))
    candidates.append(numbers[-1] + delta)

    # Exact boundary values are retained first, then the nearest interval representatives.
    priority = [*numbers, candidates[0], candidates[-1], *candidates[2:-1:2]]
    selected: list[Decimal] = []
    for value in priority:
        if value not in selected:
            selected.append(value)
        if len(selected) == limit:
            break
    return [NumberValue(kind="number", value=value, unit=unit) for value in selected]


def _predicate_signature(value: TypedValue, nodes: list[AstNode]) -> tuple[str, ...]:
    signatures: list[str] = []
    for node in nodes:
        expected = node.value
        outcome: bool | None
        if isinstance(value, NumberValue):
            if node.op is AstOperator.IN:
                candidates = {item.value for item in node.values if isinstance(item, NumberValue)}
                outcome = value.value in candidates
            elif isinstance(expected, NumberValue):
                comparisons = {
                    AstOperator.EQ: value.value == expected.value,
                    AstOperator.GTE: value.value >= expected.value,
                    AstOperator.GT: value.value > expected.value,
                    AstOperator.LTE: value.value <= expected.value,
                    AstOperator.LT: value.value < expected.value,
                }
                outcome = comparisons.get(node.op)
            elif isinstance(expected, RangeValue):
                lower = expected.lower
                upper = expected.upper
                outcome = (lower is None or value.value >= lower) and (
                    upper is None or value.value <= upper
                )
            else:
                outcome = None
        elif isinstance(value, DateValue) and isinstance(expected, DateValue):
            comparisons = {
                AstOperator.EQ: value.value == expected.value,
                AstOperator.BEFORE: value.value < expected.value,
                AstOperator.AFTER: value.value > expected.value,
            }
            outcome = comparisons.get(node.op)
        elif isinstance(value, DurationValue) and isinstance(expected, DurationValue):
            outcome = (
                value.days >= expected.days
                if node.op is AstOperator.DURATION_AT_LEAST_DAYS
                else value.days == expected.days
            )
        elif isinstance(value, BooleanValue) and isinstance(expected, BooleanValue):
            outcome = value.value == expected.value
        elif isinstance(value, (CategoricalValue, StringValue)):
            normalized = (
                value.value.strip().casefold()
                if isinstance(value, CategoricalValue)
                else (value.normalized or value.value).strip().casefold()
            )
            expected_values = []
            if expected is not None:
                expected_values.append(expected)
            expected_values.extend(node.values)
            expected_normalized = {
                item.value.strip().casefold()
                for item in expected_values
                if isinstance(item, (CategoricalValue, StringValue))
            }
            outcome = normalized in expected_normalized
        else:
            outcome = None
        signatures.append("N" if outcome is None else ("T" if outcome else "F"))
    return tuple(signatures) or (repr(value),)


def _deduplicate_equivalent[T: TypedValue](values: list[T], nodes: list[AstNode]) -> list[T]:
    result: list[T] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        signature = _predicate_signature(value, nodes)
        if signature not in seen:
            seen.add(signature)
            result.append(value)
    return result


def _date_thresholds(nodes: list[AstNode]) -> list[date]:
    values: set[date] = set()
    for node in nodes:
        candidates: list[TypedValue] = [*node.values]
        if node.value is not None:
            candidates.append(node.value)
        values.update(value.value for value in candidates if isinstance(value, DateValue))
    return sorted(values)


def _duration_thresholds(nodes: list[AstNode]) -> list[int]:
    values: set[int] = set()
    for node in nodes:
        candidates: list[TypedValue] = [*node.values]
        if node.value is not None:
            candidates.append(node.value)
        values.update(value.days for value in candidates if isinstance(value, DurationValue))
        metadata_days = node.metadata.get("days")
        if isinstance(metadata_days, int):
            values.add(metadata_days)
    return sorted(values)


def build_branches(
    *,
    question_id: str,
    slot: SlotDefinition,
    affected_criteria: list[CompiledCriterion],
    evaluation_date: date,
    conflicted: bool = False,
    replacement_value: TypedValue | None = None,
    max_branches: int = 6,
) -> list[AnswerBranch]:
    if not 2 <= max_branches <= 6:
        raise ValueError("max_branches must be between 2 and 6")
    if conflicted:
        values: list[tuple[str, str, TypedValue | None]] = [
            ("retain_fact_a", "RETAIN_A", None),
            ("retain_fact_b", "RETAIN_B", None),
        ]
        if replacement_value is not None and max_branches >= 4:
            values.append(("replacement_record_value", "VALUE", replacement_value))
        values.append(("unresolved_needs_review", "REVIEW", None))
        return _answer_branches(question_id, values[:max_branches])

    nodes = _slot_nodes(affected_criteria, slot.slot_id)
    values = []
    if slot.value_type == "boolean":
        values = [
            ("true", "VALUE", BooleanValue(kind="boolean", value=True)),
            ("false", "VALUE", BooleanValue(kind="boolean", value=False)),
            ("unknown_or_declined", "UNKNOWN", None),
        ]
    elif slot.value_type in {"categorical", "categorical_free_string"}:
        referenced = _categorical_values(nodes)
        if slot.value_type == "categorical":
            normalized_referenced: list[CategoricalValue | StringValue] = []
            for value in referenced:
                canonical = (
                    value.value
                    if isinstance(value, CategoricalValue)
                    else (value.normalized or value.value)
                )
                if slot.canonical_values and canonical not in slot.canonical_values:
                    continue
                normalized_referenced.append(CategoricalValue(kind="categorical", value=canonical))
            referenced = normalized_referenced
        values = [
            (
                value.value,
                "VALUE",
                value,
            )
            for value in referenced[: max(0, max_branches - 2)]
        ]
        other: TypedValue
        if slot.value_type == "categorical":
            canonical_other = (
                "other" if "other" in slot.canonical_values else slot.canonical_values[-1]
            )
            other = CategoricalValue(kind="categorical", value=canonical_other)
        else:
            other = StringValue(kind="string", value="other", normalized="other")
        values.extend([("other", "VALUE", other), ("unknown_or_declined", "UNKNOWN", None)])
    elif slot.value_type == "number":
        representatives = _deduplicate_equivalent(
            _numeric_representatives(_numeric_thresholds(nodes), slot, max_branches - 1),
            nodes,
        )
        values = [
            (f"synthetic:{item.value} {item.unit or ''}".strip(), "VALUE", item)
            for item in representatives
        ]
        values.append(("unknown_or_declined", "UNKNOWN", None))
    elif slot.value_type == "date":
        thresholds = _date_thresholds(nodes) or [evaluation_date]
        prioritized_dates = [
            boundary + offset
            for offset in (timedelta(0), timedelta(days=-1), timedelta(days=1))
            for boundary in thresholds
        ]
        date_values = [
            DateValue(kind="date", value=item, precision="DAY")
            for item in dict.fromkeys(prioritized_dates)
        ]
        selected = _deduplicate_equivalent(date_values, nodes)[: max_branches - 1]
        values = [
            (
                f"synthetic:{item.value.isoformat()}",
                "VALUE",
                item,
            )
            for item in selected
        ]
        values.append(("unknown_or_declined", "UNKNOWN", None))
    else:
        duration_thresholds = _duration_thresholds(nodes) or [0]
        durations = sorted(
            {max(0, boundary + offset) for boundary in duration_thresholds for offset in (-1, 0, 1)}
        )
        duration_values = _deduplicate_equivalent(
            [DurationValue(kind="duration", days=item) for item in durations], nodes
        )[: max_branches - 1]
        values = [
            (
                f"synthetic:{item.days} days",
                "VALUE",
                item,
            )
            for item in duration_values
        ]
        values.append(("unknown_or_declined", "UNKNOWN", None))
    return _answer_branches(question_id, values[:max_branches])
