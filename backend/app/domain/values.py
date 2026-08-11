from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class BooleanValue(StrictModel):
    kind: Literal["boolean"]
    value: bool


class NumberValue(StrictModel):
    kind: Literal["number"]
    value: Decimal
    unit: str | None = None


class StringValue(StrictModel):
    kind: Literal["string"]
    value: str
    normalized: str | None = None


class CategoricalValue(StrictModel):
    kind: Literal["categorical"]
    value: str
    system: str | None = None


class DateValue(StrictModel):
    kind: Literal["date"]
    value: date
    precision: Literal["DAY", "MONTH", "YEAR"]


class DurationValue(StrictModel):
    kind: Literal["duration"]
    days: int


class RangeValue(StrictModel):
    kind: Literal["range"]
    lower: Decimal | None = None
    upper: Decimal | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    unit: str | None = None

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> RangeValue:
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("range lower bound cannot exceed upper bound")
        return self


class UnknownValue(StrictModel):
    kind: Literal["unknown"]
    reason: str


TypedValue = Annotated[
    Union[
        BooleanValue,
        NumberValue,
        StringValue,
        CategoricalValue,
        DateValue,
        DurationValue,
        RangeValue,
        UnknownValue,
    ],
    Field(discriminator="kind"),
]
