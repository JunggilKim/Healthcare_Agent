from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.app.infrastructure.rate_limiter import FixedWindowRateLimiter
from backend.app.infrastructure.usage_guard import (
    CostGuardExceeded,
    InMemoryUsageGuard,
    default_pricing_estimator,
)


def test_no_retry_eight_trial_cold_path_fits_and_crossing_retry_is_blocked() -> None:
    pricing = default_pricing_estimator()
    compiler = pricing.reserved_generation_cost(
        "gemini-3.6-flash", estimated_input_tokens=12_000, max_output_tokens=4_000
    )
    reviewer = pricing.reserved_generation_cost(
        "gemini-3.6-flash", estimated_input_tokens=12_000, max_output_tokens=1_500
    )
    no_retry_cold_path = Decimal(8) * (compiler + reviewer)
    assert no_retry_cold_path < Decimal("1.25")

    guard = InMemoryUsageGuard(session_cap_usd=Decimal("1.25"))
    guard.reserve(session_id="cold-session", amount_usd=no_retry_cold_path)
    crossing_retry = Decimal("1.25") - no_retry_cold_path + Decimal("0.001")
    with pytest.raises(CostGuardExceeded, match="SESSION_COST_CAP_EXCEEDED"):
        guard.reserve(session_id="cold-session", amount_usd=crossing_retry)


def test_stale_reservation_expires_after_twenty_minutes() -> None:
    current = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    guard = InMemoryUsageGuard(session_cap_usd=Decimal("1.25"), now=lambda: current)
    reservation = guard.reserve(session_id="session", amount_usd=Decimal("1.20"))
    assert reservation.amount_usd == Decimal("1.20")
    current += timedelta(minutes=21)
    guard.reserve(session_id="session", amount_usd=Decimal("1.20"))


def test_rate_limiter_uses_hash_only_and_enforces_fixed_limit() -> None:
    limiter = FixedWindowRateLimiter(salt="test-salt")
    results = [limiter.consume("203.0.113.7", "live_session") for _ in range(6)]
    assert all(result.allowed for result in results[:5])
    assert not results[5].allowed
    assert "203.0.113.7" not in limiter.stored_subjects()
    assert results[0].subject_hash in limiter.stored_subjects()
