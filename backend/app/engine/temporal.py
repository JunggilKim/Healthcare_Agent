from __future__ import annotations

from datetime import date


def completed_years(date_of_birth: date, on_date: date) -> int:
    if date_of_birth > on_date:
        raise ValueError("date of birth cannot be after evaluation date")
    return (
        on_date.year
        - date_of_birth.year
        - ((on_date.month, on_date.day) < (date_of_birth.month, date_of_birth.day))
    )


def directional_days(event_date: date, reference_date: date, direction: str) -> int | None:
    if direction == "BEFORE_OR_ON":
        return (reference_date - event_date).days if event_date <= reference_date else None
    if direction == "AFTER_OR_ON":
        return (event_date - reference_date).days if event_date >= reference_date else None
    raise ValueError(f"unsupported temporal direction: {direction}")
