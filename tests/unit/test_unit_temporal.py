from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.engine.temporal import completed_years, directional_days
from backend.app.engine.unit_converter import UnitConversionError, default_unit_converter


def test_only_whitelisted_unit_conversions_are_performed() -> None:
    converter = default_unit_converter()
    assert converter.convert(Decimal("1000"), "mg", "g") == Decimal("1")
    assert converter.convert(Decimal("2"), "weeks", "day") == Decimal("14")
    assert converter.convert(Decimal("30"), "ml/min", "mL/min") == Decimal("30")
    with pytest.raises(UnitConversionError, match="incompatible"):
        converter.convert(Decimal("1"), "kg", "day")
    with pytest.raises(UnitConversionError, match="unsupported"):
        converter.convert(Decimal("1"), "mmHg", "day")


def test_age_and_directional_date_boundaries() -> None:
    assert completed_years(date(2000, 8, 12), date(2026, 8, 11)) == 25
    assert completed_years(date(2000, 8, 11), date(2026, 8, 11)) == 26
    assert directional_days(date(2026, 8, 1), date(2026, 8, 11), "BEFORE_OR_ON") == 10
    assert directional_days(date(2026, 8, 12), date(2026, 8, 11), "BEFORE_OR_ON") is None
