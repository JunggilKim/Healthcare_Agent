from __future__ import annotations

from collections.abc import Mapping

from backend.app.domain.sessions import SessionState


class StateTransitionError(ValueError):
    """Raised before persistence when a session transition is not in the frozen table."""


_PRIMARY_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.INPUT_VALIDATING}),
    SessionState.INPUT_VALIDATING: frozenset({SessionState.PATIENT_EXTRACTING}),
    SessionState.PATIENT_EXTRACTING: frozenset({SessionState.RETRIEVING}),
    SessionState.RETRIEVING: frozenset({SessionState.CANDIDATES_READY}),
    SessionState.CANDIDATES_READY: frozenset({SessionState.COMPILING}),
    SessionState.COMPILING: frozenset({SessionState.EVALUATING}),
    SessionState.EVALUATING: frozenset({SessionState.VERIFYING}),
    SessionState.VERIFYING: frozenset({SessionState.RANKING}),
    SessionState.RANKING: frozenset({SessionState.QUESTION_SELECTING}),
    SessionState.QUESTION_SELECTING: frozenset(
        {SessionState.QUESTION_READY, SessionState.COMPLETE}
    ),
    SessionState.QUESTION_READY: frozenset({SessionState.ANSWER_INTERPRETING}),
    SessionState.ANSWER_INTERPRETING: frozenset({SessionState.REEVALUATING}),
    SessionState.REEVALUATING: frozenset({SessionState.VERIFYING}),
    SessionState.COMPLETE: frozenset(),
    SessionState.DEGRADED: frozenset(
        state
        for state in SessionState
        if state not in {SessionState.CREATED, SessionState.RESET, SessionState.DEGRADED}
    ),
    SessionState.FAILED: frozenset(),
    SessionState.RESET: frozenset(),
}


def transition_table() -> Mapping[SessionState, frozenset[SessionState]]:
    return _PRIMARY_TRANSITIONS


def validate_transition(current: SessionState, target: SessionState) -> None:
    """Validate a transition, including the global DEGRADED/FAILED/RESET exits."""

    if current in {SessionState.RESET, SessionState.FAILED}:
        allowed = _PRIMARY_TRANSITIONS[current]
    else:
        allowed = _PRIMARY_TRANSITIONS[current] | frozenset(
            {SessionState.DEGRADED, SessionState.FAILED, SessionState.RESET}
        )
    if target not in allowed:
        raise StateTransitionError(f"illegal session transition: {current.value} -> {target.value}")
