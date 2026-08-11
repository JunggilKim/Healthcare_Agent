from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentifierMatch:
    category: str
    start: int
    end: int


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")),
    ("KOREAN_RRN", re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)")),
    ("US_SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "EXPLICIT_IDENTIFIER_LABEL",
        re.compile(r"\b(?:name|patient\s*id|mrn)\s*:|(?:주민등록번호|환자번호)\s*:", re.IGNORECASE),
    ),
)


def detect_identifier_ranges(text: str) -> list[IdentifierMatch]:
    matches = [
        IdentifierMatch(category=category, start=match.start(), end=match.end())
        for category, pattern in _PATTERNS
        for match in pattern.finditer(text)
    ]
    return sorted(matches, key=lambda item: (item.start, item.end, item.category))
