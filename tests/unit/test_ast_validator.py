from __future__ import annotations

from datetime import date

import pytest

from backend.app.application.catalog import load_slot_catalog
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.values import DateValue, DurationValue, NumberValue, RangeValue, StringValue
from backend.app.engine.ast_validator import AstValidationError, validate_ast_shape


def _ast(node: AstNode, children: list[AstNode] | None = None) -> CriterionAst:
    return CriterionAst(root_node_id=node.node_id, nodes=[node, *(children or [])])


def test_not_with_two_children_is_rejected() -> None:
    root = AstNode(node_id="n0", op=AstOperator.NOT, child_ids=["n1", "n2"])
    children = [
        AstNode(node_id="n1", op=AstOperator.EXISTS, slot_id="demographics.age"),
        AstNode(node_id="n2", op=AstOperator.EXISTS, slot_id="demographics.age"),
    ]
    with pytest.raises(AstValidationError, match="NOT requires exactly one child"):
        validate_ast_shape(_ast(root, children), load_slot_catalog().by_id())


def test_gte_with_string_is_rejected() -> None:
    node = AstNode(
        node_id="n0",
        op=AstOperator.GTE,
        slot_id="demographics.age",
        value=StringValue(kind="string", value="18"),
    )
    with pytest.raises(AstValidationError, match="numeric comparison value"):
        validate_ast_shape(_ast(node), load_slot_catalog().by_id())


def test_between_with_open_bound_is_rejected() -> None:
    node = AstNode(
        node_id="n0",
        op=AstOperator.BETWEEN_INCLUSIVE,
        slot_id="demographics.age",
        value=RangeValue(
            kind="range",
            lower=None,
            upper="65",
            lower_inclusive=True,
            upper_inclusive=True,
            unit="year",
        ),
    )
    with pytest.raises(AstValidationError, match="closed bounds"):
        validate_ast_shape(_ast(node), load_slot_catalog().by_id())


def test_within_days_requires_direction() -> None:
    node = AstNode(
        node_id="n0",
        op=AstOperator.WITHIN_DAYS,
        slot_id="prior_treatment.last_systemic_anticancer_date",
        value=DurationValue(kind="duration", days=28),
        metadata={"reference_kind": "EVALUATION_DATE"},
    )
    with pytest.raises(AstValidationError, match="evaluation metadata"):
        validate_ast_shape(_ast(node), load_slot_catalog().by_id())


def test_before_cannot_have_fixed_date_and_reference_slot() -> None:
    node = AstNode(
        node_id="n0",
        op=AstOperator.BEFORE,
        slot_id="procedure.last_major_surgery_date",
        value=DateValue(kind="date", value=date(2026, 8, 11), precision="DAY"),
        metadata={
            "reference_kind": "SLOT",
            "reference_slot_id": "prior_treatment.last_systemic_anticancer_date",
            "inclusive": False,
        },
    )
    with pytest.raises(AstValidationError, match="fixed date comparison metadata"):
        validate_ast_shape(_ast(node), load_slot_catalog().by_id())


def test_unknown_metadata_key_is_rejected() -> None:
    node = AstNode(
        node_id="n0",
        op=AstOperator.GTE,
        slot_id="demographics.age",
        value=NumberValue(kind="number", value="18", unit="year"),
        metadata={"invented": True},
    )
    with pytest.raises(AstValidationError, match="numeric comparison metadata"):
        validate_ast_shape(_ast(node), load_slot_catalog().by_id())
