from __future__ import annotations

from enum import Enum

from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.domain.values import JsonScalar, TypedValue


class AstOperator(str, Enum):
    ALL = "ALL"
    ANY = "ANY"
    NOT = "NOT"
    IMPLIES = "IMPLIES"
    EXISTS = "EXISTS"
    EQ = "EQ"
    IN = "IN"
    GTE = "GTE"
    GT = "GT"
    LTE = "LTE"
    LT = "LT"
    BETWEEN_INCLUSIVE = "BETWEEN_INCLUSIVE"
    WITHIN_DAYS = "WITHIN_DAYS"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    DURATION_AT_LEAST_DAYS = "DURATION_AT_LEAST_DAYS"
    IS_A = "IS_A"
    OPAQUE = "OPAQUE"


class AstNode(StrictModel):
    node_id: str
    op: AstOperator
    child_ids: list[str] = Field(default_factory=list)
    slot_id: str | None = None
    value: TypedValue | None = None
    values: list[TypedValue] = Field(default_factory=list)
    unit: str | None = None
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)


class CriterionAst(StrictModel):
    root_node_id: str
    nodes: list[AstNode]
