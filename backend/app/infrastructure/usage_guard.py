from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from backend.app.domain.canonical import load_yaml
from backend.app.settings import REPOSITORY_ROOT


class _TokenPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Decimal
    output_reasoning: Decimal


class _EmbeddingPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online_per_1000_input_tokens: Decimal
    batch_per_1000_input_tokens: Decimal


class _PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    effective_date: date
    currency: str
    source: str
    standard_paygo_global_per_million_tokens: dict[str, _TokenPrice]
    batch_global_per_million_tokens: dict[str, _TokenPrice]
    embedding: dict[str, _EmbeddingPrice]


@dataclass(frozen=True)
class PricingEstimator:
    config: _PricingConfig

    @classmethod
    def from_path(cls, path: Path) -> PricingEstimator:
        return cls(_PricingConfig.model_validate(load_yaml(path)))

    def generation_cost(
        self,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        batch: bool = False,
    ) -> Decimal:
        prices = (
            self.config.batch_global_per_million_tokens
            if batch
            else self.config.standard_paygo_global_per_million_tokens
        )
        if model_id not in prices:
            raise ValueError(f"no configured generation price for {model_id}")
        price = prices[model_id]
        return (
            Decimal(input_tokens) * price.input
            + Decimal(output_tokens + reasoning_tokens) * price.output_reasoning
        ) / Decimal(1_000_000)

    def reserved_generation_cost(
        self, model_id: str, *, estimated_input_tokens: int, max_output_tokens: int
    ) -> Decimal:
        return self.generation_cost(
            model_id,
            input_tokens=estimated_input_tokens,
            output_tokens=max_output_tokens,
        )


def default_pricing_estimator() -> PricingEstimator:
    return PricingEstimator.from_path(REPOSITORY_ROOT / "config" / "pricing.yaml")


class CostGuardExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class CostReservation:
    reservation_id: str
    session_id: str
    amount_usd: Decimal
    created_at: datetime


@dataclass(frozen=True)
class UsageSnapshot:
    session_reserved_usd: Decimal
    session_reconciled_usd: Decimal
    daily_reserved_usd: Decimal
    daily_reconciled_usd: Decimal
    total_reconciled_usd: Decimal


class InMemoryUsageGuard:
    """Transactional local mirror of the Firestore cost-reservation contract."""

    def __init__(
        self,
        *,
        session_cap_usd: Decimal = Decimal("1.25"),
        daily_cap_usd: Decimal = Decimal("10.00"),
        total_cap_usd: Decimal = Decimal("180.00"),
        stale_after: timedelta = timedelta(minutes=20),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_cap_usd = session_cap_usd
        self.daily_cap_usd = daily_cap_usd
        self.total_cap_usd = total_cap_usd
        self.stale_after = stale_after
        self._now = now
        self._reservations: dict[str, CostReservation] = {}
        self._session_reconciled: dict[str, Decimal] = {}
        self._daily_reconciled: dict[date, Decimal] = {}
        self._total_reconciled = Decimal(0)
        self._lock = threading.RLock()

    def _expire_stale(self, now: datetime) -> None:
        cutoff = now - self.stale_after
        self._reservations = {
            key: reservation
            for key, reservation in self._reservations.items()
            if reservation.created_at > cutoff
        }

    def reserve(self, *, session_id: str, amount_usd: Decimal) -> CostReservation:
        if amount_usd <= 0:
            raise ValueError("reservation amount must be positive")
        with self._lock:
            now = self._now().astimezone(UTC)
            self._expire_stale(now)
            session_reserved = sum(
                (
                    reservation.amount_usd
                    for reservation in self._reservations.values()
                    if reservation.session_id == session_id
                ),
                start=Decimal(0),
            )
            daily_reserved = sum(
                (
                    reservation.amount_usd
                    for reservation in self._reservations.values()
                    if reservation.created_at.date() == now.date()
                ),
                start=Decimal(0),
            )
            total_reserved = sum(
                (reservation.amount_usd for reservation in self._reservations.values()),
                start=Decimal(0),
            )
            session_projected = (
                self._session_reconciled.get(session_id, Decimal(0)) + session_reserved + amount_usd
            )
            daily_projected = (
                self._daily_reconciled.get(now.date(), Decimal(0)) + daily_reserved + amount_usd
            )
            if session_projected > self.session_cap_usd:
                raise CostGuardExceeded("SESSION_COST_CAP_EXCEEDED")
            if daily_projected > self.daily_cap_usd:
                raise CostGuardExceeded("DAILY_COST_CAP_EXCEEDED")
            if self._total_reconciled + total_reserved + amount_usd > self.total_cap_usd:
                raise CostGuardExceeded("TOTAL_COST_CAP_EXCEEDED")
            reservation = CostReservation(
                reservation_id=f"reservation_{uuid4()}",
                session_id=session_id,
                amount_usd=amount_usd,
                created_at=now,
            )
            self._reservations[reservation.reservation_id] = reservation
            return reservation

    def reconcile(self, reservation_id: str, *, actual_usd: Decimal) -> None:
        if actual_usd < 0:
            raise ValueError("actual cost cannot be negative")
        with self._lock:
            reservation = self._reservations.pop(reservation_id)
            day = reservation.created_at.date()
            self._session_reconciled[reservation.session_id] = (
                self._session_reconciled.get(reservation.session_id, Decimal(0)) + actual_usd
            )
            self._daily_reconciled[day] = self._daily_reconciled.get(day, Decimal(0)) + actual_usd
            self._total_reconciled += actual_usd

    def release(self, reservation_id: str) -> None:
        with self._lock:
            self._reservations.pop(reservation_id, None)

    def snapshot(self, session_id: str) -> UsageSnapshot:
        with self._lock:
            now = self._now().astimezone(UTC)
            self._expire_stale(now)
            return UsageSnapshot(
                session_reserved_usd=sum(
                    (
                        reservation.amount_usd
                        for reservation in self._reservations.values()
                        if reservation.session_id == session_id
                    ),
                    start=Decimal(0),
                ),
                session_reconciled_usd=self._session_reconciled.get(session_id, Decimal(0)),
                daily_reserved_usd=sum(
                    (
                        reservation.amount_usd
                        for reservation in self._reservations.values()
                        if reservation.created_at.date() == now.date()
                    ),
                    start=Decimal(0),
                ),
                daily_reconciled_usd=self._daily_reconciled.get(now.date(), Decimal(0)),
                total_reconciled_usd=self._total_reconciled,
            )
