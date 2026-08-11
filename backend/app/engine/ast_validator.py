from __future__ import annotations

import re
from collections.abc import Mapping

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    RangeValue,
    StringValue,
    UnknownValue,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AstValidationError(ValueError):
    """The proposed AST cannot be persisted or executed safely."""


def _reject(condition: bool, message: str) -> None:
    if condition:
        raise AstValidationError(message)


def _validate_leaf_slot(node: AstNode, slots: Mapping[str, SlotDefinition]) -> SlotDefinition:
    _reject(node.slot_id is None, f"{node.op.value} requires slot_id")
    assert node.slot_id is not None
    _reject(node.slot_id not in slots, f"unknown slot_id: {node.slot_id}")
    return slots[node.slot_id]


def _validate_node_shape(node: AstNode, slots: Mapping[str, SlotDefinition]) -> None:
    _reject(node.unit is not None, f"{node.node_id}: AstNode.unit must be null")
    op = node.op
    child_count = len(node.child_ids)
    if op in {AstOperator.ALL, AstOperator.ANY}:
        _reject(not 1 <= child_count <= 64, f"{op.value} requires 1-64 children")
        _reject(
            node.slot_id is not None or node.value is not None or bool(node.values),
            "aggregate payload",
        )
        _reject(bool(node.metadata), f"{op.value} metadata must be empty")
        return
    if op is AstOperator.NOT:
        _reject(child_count != 1, "NOT requires exactly one child")
        _reject(
            node.slot_id is not None or node.value is not None or bool(node.values),
            "NOT payload",
        )
        _reject(bool(node.metadata), "NOT metadata must be empty")
        return
    if op is AstOperator.IMPLIES:
        _reject(child_count != 2, "IMPLIES requires exactly two children")
        _reject(
            node.slot_id is not None or node.value is not None or bool(node.values),
            "IMPLIES payload",
        )
        _reject(
            node.metadata != {"antecedent_index": 0, "consequent_index": 1},
            "IMPLIES metadata is invalid",
        )
        return

    _reject(child_count != 0, f"{op.value} leaf cannot have children")
    if op is AstOperator.OPAQUE:
        _reject(
            node.slot_id is not None or node.value is not None or bool(node.values),
            "OPAQUE payload",
        )
        _reject(set(node.metadata) != {"reason_code", "residual_source_sha256"}, "OPAQUE metadata")
        residual_hash = node.metadata.get("residual_source_sha256")
        _reject(
            not isinstance(residual_hash, str) or not _SHA256_PATTERN.fullmatch(residual_hash),
            "OPAQUE hash",
        )
        return

    slot = _validate_leaf_slot(node, slots)
    if op is AstOperator.EXISTS:
        _reject(
            node.value is not None or bool(node.values) or bool(node.metadata),
            "EXISTS payload",
        )
        return
    if op is AstOperator.EQ:
        _reject(node.value is None or bool(node.values) or bool(node.metadata), "EQ payload")
        _reject(isinstance(node.value, (RangeValue, UnknownValue)), "EQ value kind")
    elif op is AstOperator.IN:
        _reject(node.value is not None or not 1 <= len(node.values) <= 32, "IN payload")
        _reject(bool(node.metadata), "IN metadata must be empty")
        kinds = {value.kind for value in node.values}
        _reject(len(kinds) != 1 or "unknown" in kinds or "range" in kinds, "IN value kinds")
    elif op in {AstOperator.GTE, AstOperator.GT, AstOperator.LTE, AstOperator.LT}:
        _reject(
            not isinstance(node.value, NumberValue) or bool(node.values), "numeric comparison value"
        )
        _reject(bool(node.metadata), "numeric comparison metadata")
        _reject(slot.value_type != "number", "numeric comparison requires numeric slot")
    elif op is AstOperator.BETWEEN_INCLUSIVE:
        _reject(not isinstance(node.value, RangeValue) or bool(node.values), "BETWEEN value")
        assert isinstance(node.value, RangeValue)
        _reject(
            node.value.lower is None
            or node.value.upper is None
            or not node.value.lower_inclusive
            or not node.value.upper_inclusive,
            "BETWEEN_INCLUSIVE requires closed bounds",
        )
        _reject(bool(node.metadata) or slot.value_type != "number", "BETWEEN metadata/type")
    elif op is AstOperator.WITHIN_DAYS:
        _reject(
            slot.value_type != "date" or not isinstance(node.value, DurationValue),
            "WITHIN_DAYS type",
        )
        assert isinstance(node.value, DurationValue)
        _reject(node.value.days < 0 or bool(node.values), "WITHIN_DAYS duration")
        kind = node.metadata.get("reference_kind")
        if kind == "EVALUATION_DATE":
            _reject(
                node.metadata != {"reference_kind": "EVALUATION_DATE", "direction": "BEFORE_OR_ON"},
                "WITHIN_DAYS evaluation metadata",
            )
        elif kind == "SLOT":
            _reject(
                set(node.metadata) != {"reference_kind", "reference_slot_id", "direction"},
                "WITHIN_DAYS slot metadata",
            )
            reference_slot_id = node.metadata.get("reference_slot_id")
            _reject(
                not isinstance(reference_slot_id, str)
                or reference_slot_id not in slots
                or slots[reference_slot_id].value_type != "date",
                "WITHIN_DAYS reference slot",
            )
            _reject(
                node.metadata.get("direction") not in {"BEFORE_OR_ON", "AFTER_OR_ON"},
                "WITHIN_DAYS direction",
            )
        else:
            raise AstValidationError("WITHIN_DAYS requires explicit reference_kind and direction")
    elif op in {AstOperator.BEFORE, AstOperator.AFTER}:
        _reject(slot.value_type != "date" or bool(node.values), "date ordering slot")
        if isinstance(node.value, DateValue):
            _reject(bool(node.metadata), "fixed date comparison metadata")
        elif node.value is None:
            _reject(
                set(node.metadata) != {"reference_kind", "reference_slot_id", "inclusive"},
                "date slot reference metadata",
            )
            reference_slot_id = node.metadata.get("reference_slot_id")
            _reject(node.metadata.get("reference_kind") != "SLOT", "date reference kind")
            _reject(
                not isinstance(reference_slot_id, str)
                or reference_slot_id not in slots
                or slots[reference_slot_id].value_type != "date",
                "date reference slot",
            )
            _reject(not isinstance(node.metadata.get("inclusive"), bool), "date inclusive flag")
        else:
            raise AstValidationError("BEFORE/AFTER requires a fixed date or SLOT reference")
    elif op is AstOperator.DURATION_AT_LEAST_DAYS:
        _reject(
            not isinstance(node.value, DurationValue) or bool(node.values) or bool(node.metadata),
            "duration payload",
        )
    elif op is AstOperator.IS_A:
        _reject(not isinstance(node.value, CategoricalValue) or bool(node.values), "IS_A value")
        _reject(node.metadata != {"ontology_version": "ontology-whitelist-v1"}, "IS_A metadata")
    else:
        raise AstValidationError(f"unhandled AST operator: {op.value}")

    if slot.value_type == "boolean":
        _reject(
            not isinstance(node.value, BooleanValue) and op is not AstOperator.IN,
            "boolean slot value",
        )
    elif slot.value_type == "number" and op in {AstOperator.EQ, AstOperator.IN}:
        values = [node.value] if node.value is not None else node.values
        _reject(any(not isinstance(value, NumberValue) for value in values), "number slot value")
    elif slot.value_type in {"categorical", "categorical_free_string"} and op in {
        AstOperator.EQ,
        AstOperator.IN,
    }:
        values = [node.value] if node.value is not None else node.values
        _reject(
            any(not isinstance(value, (CategoricalValue, StringValue)) for value in values),
            "categorical slot value",
        )


def validate_ast_shape(ast: CriterionAst, slots: Mapping[str, SlotDefinition]) -> None:
    """Apply the one authoritative graph and operator-shape validator."""

    _reject(not ast.nodes, "AST must contain at least one node")
    _reject(len(ast.nodes) > 128, "AST cannot exceed 128 nodes")
    node_by_id = {node.node_id: node for node in ast.nodes}
    _reject(len(node_by_id) != len(ast.nodes), "duplicate AST node IDs")
    _reject(ast.root_node_id not in node_by_id, "missing AST root node")
    for node in ast.nodes:
        _reject(any(child_id not in node_by_id for child_id in node.child_ids), "dangling child ID")
        _validate_node_shape(node, slots)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, depth: int) -> None:
        _reject(depth > 16, "AST depth exceeds 16")
        _reject(node_id in visiting, "AST cycle detected")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in node_by_id[node_id].child_ids:
            visit(child_id, depth + 1)
        visiting.remove(node_id)
        visited.add(node_id)

    visit(ast.root_node_id, 1)
    _reject(visited != set(node_by_id), "unreachable AST node")
