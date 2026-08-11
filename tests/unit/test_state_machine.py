from __future__ import annotations

import pytest

from backend.app.application.state_machine import StateTransitionError, validate_transition
from backend.app.domain.sessions import SessionState


def test_legal_pipeline_transition() -> None:
    validate_transition(SessionState.CREATED, SessionState.INPUT_VALIDATING)


def test_any_running_state_can_degrade() -> None:
    validate_transition(SessionState.RETRIEVING, SessionState.DEGRADED)
    validate_transition(SessionState.DEGRADED, SessionState.CANDIDATES_READY)


def test_invalid_transition_fails_with_exact_states() -> None:
    with pytest.raises(
        StateTransitionError,
        match="illegal session transition: CREATED -> RANKING",
    ):
        validate_transition(SessionState.CREATED, SessionState.RANKING)
