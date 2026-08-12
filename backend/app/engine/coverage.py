from __future__ import annotations

import re
from dataclasses import dataclass

from backend.app.domain.model_outputs import CriterionCompilationProposal

_HEADING = re.compile(r"^\s*(?:inclusion|exclusion) criteria\s*:?\s*$", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•°]|\d+\\?[.)])\s*")


@dataclass(frozen=True)
class CoverageReport:
    assigned_non_whitespace_characters: int
    eligible_non_whitespace_characters: int
    ratio: float
    uncovered_ranges: list[tuple[int, int]]


def _eligible_indexes(source: str) -> set[int]:
    eligible: set[int] = set()
    offset = 0
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if not _HEADING.fullmatch(content):
            marker_end = _LIST_MARKER.match(content)
            content_start = marker_end.end() if marker_end else 0
            eligible.update(
                offset + index
                for index, character in enumerate(content)
                if index >= content_start and not character.isspace()
            )
        offset += len(line)
    return eligible


def calculate_source_coverage(
    source: str, criteria: list[CriterionCompilationProposal]
) -> CoverageReport:
    eligible = _eligible_indexes(source)
    assigned: set[int] = set()
    for criterion in criteria:
        assigned.update(range(criterion.start, criterion.end))
    covered = eligible & assigned
    uncovered = sorted(eligible - covered)
    ranges: list[tuple[int, int]] = []
    if uncovered:
        start = previous = uncovered[0]
        for index in uncovered[1:]:
            if index != previous + 1:
                ranges.append((start, previous + 1))
                start = index
            previous = index
        ranges.append((start, previous + 1))
    denominator = len(eligible)
    ratio = len(covered) / denominator if denominator else 1.0
    return CoverageReport(
        assigned_non_whitespace_characters=len(covered),
        eligible_non_whitespace_characters=denominator,
        ratio=ratio,
        uncovered_ranges=ranges,
    )
