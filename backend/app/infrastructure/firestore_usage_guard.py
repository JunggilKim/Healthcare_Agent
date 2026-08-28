from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar
from uuid import uuid4

from google.api_core.exceptions import Aborted, DeadlineExceeded, ServiceUnavailable
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.app.infrastructure.usage_guard import CostGuardExceeded, CostReservation

logger = logging.getLogger("trial_opt.model")
T = TypeVar("T")


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _legacy_active_reservations(data: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    """Retain old embedded reservations until they expire during rolling migration."""

    reservations = data.get("reservations", {})
    return {
        key: value
        for key, value in reservations.items()
        if datetime.fromisoformat(str(value["expires_at"])) > now
    }


def _usage_amounts(
    data: dict[str, Any], now: datetime
) -> tuple[Decimal, Decimal, Decimal, dict[str, dict[str, Any]]]:
    legacy = _legacy_active_reservations(data, now)
    legacy_reserved = sum((_decimal(item["amount_usd"]) for item in legacy.values()), Decimal(0))
    return (
        _decimal(data.get("reconciled_usd")),
        _decimal(data.get("reserved_usd")),
        legacy_reserved,
        legacy,
    )


class FirestoreUsageGuard:
    """Distributed cost ledger with atomic caps and idempotent reservation settlement.

    Aggregate documents contain only counters. Each reservation is an immutable-keyed
    ledger document, so retries can discover a committed result and every billed call
    remains independently auditable across Cloud Run instances.
    """

    def __init__(
        self,
        client: firestore.AsyncClient,
        *,
        session_cap_usd: Decimal,
        daily_cap_usd: Decimal,
        total_cap_usd: Decimal,
        stale_after: timedelta = timedelta(minutes=20),
        transaction_attempts: int = 4,
        cleanup_interval_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if transaction_attempts < 1:
            raise ValueError("transaction_attempts must be positive")
        self.client = client
        self.session_cap_usd = session_cap_usd
        self.daily_cap_usd = daily_cap_usd
        self.total_cap_usd = total_cap_usd
        self.stale_after = stale_after
        self.transaction_attempts = transaction_attempts
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._sleep = sleep
        self._jitter = jitter
        self._transaction_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()
        self._last_cleanup_monotonic = float("-inf")

    def _aggregate_refs(self, session_id: str, day: str) -> tuple[Any, Any, Any]:
        return (
            self.client.collection("session_usage").document(session_id),
            self.client.collection("daily_usage").document(day),
            self.client.collection("usage_control").document("total"),
        )

    async def _run_transaction(
        self, callback: Callable[[Any], Awaitable[T]], *, operation: str
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(self.transaction_attempts):
            transaction = self.client.transaction(max_attempts=5)
            transactional = firestore.async_transactional(callback)
            try:
                async with self._transaction_lock:
                    return await transactional(transaction)
            except (Aborted, DeadlineExceeded, ServiceUnavailable) as error:
                last_error = error
                if attempt == self.transaction_attempts - 1:
                    break
                delay = 0.05 * (2**attempt) + float(self._jitter(0.0, 0.05))
                logger.warning(
                    "usage ledger transaction retry (operation=%s retry_count=%s error=%s)",
                    operation,
                    attempt + 1,
                    type(error).__name__,
                )
                await self._sleep(delay)
        assert last_error is not None
        raise last_error

    async def _cleanup_expired(self, now: datetime) -> None:
        monotonic_now = time.monotonic()
        if monotonic_now - self._last_cleanup_monotonic < self.cleanup_interval_seconds:
            return
        async with self._cleanup_lock:
            monotonic_now = time.monotonic()
            if monotonic_now - self._last_cleanup_monotonic < self.cleanup_interval_seconds:
                return
            self._last_cleanup_monotonic = monotonic_now
            try:
                query = (
                    self.client.collection("usage_reservations")
                    .where(filter=FieldFilter("expires_at", "<=", now))
                    .limit(25)
                )
                expired_ids = [snapshot.id async for snapshot in query.stream()]
                for reservation_id in expired_ids:
                    await self._finish(reservation_id, actual_usd=None, expired_only=True)
            except Exception as error:
                # Cap enforcement remains conservative if cleanup is temporarily
                # unavailable; stale amounts can block spend but can never permit it.
                logger.warning(
                    "usage ledger stale cleanup failed; retaining conservative counters (error=%s)",
                    type(error).__name__,
                )

    async def reserve_async(self, *, session_id: str, amount_usd: Decimal) -> CostReservation:
        if amount_usd <= 0:
            raise ValueError("reservation amount must be positive")
        now = datetime.now(UTC)
        await self._cleanup_expired(now)
        reservation = CostReservation(
            reservation_id=f"reservation_{uuid4()}",
            session_id=session_id,
            amount_usd=amount_usd,
            created_at=now,
        )
        expires_at = now + self.stale_after
        day = now.date().isoformat()
        aggregate_refs = self._aggregate_refs(session_id, day)
        reservation_ref = self.client.collection("usage_reservations").document(
            reservation.reservation_id
        )

        async def reserve(tx: Any) -> None:
            ledger_snapshot = await reservation_ref.get(transaction=tx)
            aggregate_snapshots = [
                await reference.get(transaction=tx) for reference in aggregate_refs
            ]
            if ledger_snapshot.exists:
                ledger = ledger_snapshot.to_dict() or {}
                if (
                    ledger.get("status") == "RESERVED"
                    and ledger.get("session_id") == session_id
                    and _decimal(ledger.get("amount_usd")) == amount_usd
                ):
                    return
                raise RuntimeError("USAGE_RESERVATION_ID_COLLISION")

            cap_codes = (
                (self.session_cap_usd, "SESSION_COST_CAP_EXCEEDED"),
                (self.daily_cap_usd, "DAILY_COST_CAP_EXCEEDED"),
                (self.total_cap_usd, "TOTAL_COST_CAP_EXCEEDED"),
            )
            states = []
            for snapshot, (cap, code) in zip(aggregate_snapshots, cap_codes, strict=True):
                data = snapshot.to_dict() or {}
                reconciled, reserved, legacy_reserved, legacy = _usage_amounts(data, now)
                if reconciled + reserved + legacy_reserved + amount_usd > cap:
                    raise CostGuardExceeded(code)
                states.append((reconciled, reserved, legacy))

            for reference, (reconciled, reserved, legacy) in zip(
                aggregate_refs, states, strict=True
            ):
                tx.set(
                    reference,
                    {
                        "reconciled_usd": str(reconciled),
                        "reserved_usd": str(reserved + amount_usd),
                        "reservations": legacy,
                        "updated_at": now,
                    },
                    merge=True,
                )
            tx.create(
                reservation_ref,
                {
                    "session_id": session_id,
                    "day": day,
                    "amount_usd": str(amount_usd),
                    "actual_usd": None,
                    "status": "RESERVED",
                    "created_at": now,
                    "expires_at": expires_at,
                    "settled_at": None,
                },
            )

        await self._run_transaction(reserve, operation="reserve")
        return reservation

    async def _finish(
        self,
        reservation_id: str,
        *,
        actual_usd: Decimal | None,
        expired_only: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        reservation_ref = self.client.collection("usage_reservations").document(reservation_id)

        async def finish(tx: Any) -> None:
            ledger_snapshot = await reservation_ref.get(transaction=tx)
            if not ledger_snapshot.exists:
                return
            ledger = ledger_snapshot.to_dict() or {}
            if ledger.get("status") != "RESERVED":
                return
            expires_at = ledger.get("expires_at")
            if expired_only and isinstance(expires_at, datetime) and expires_at > now:
                return
            session_id = str(ledger["session_id"])
            day = str(ledger["day"])
            amount = _decimal(ledger["amount_usd"])
            aggregate_refs = self._aggregate_refs(session_id, day)
            aggregate_snapshots = [
                await reference.get(transaction=tx) for reference in aggregate_refs
            ]
            for reference, snapshot in zip(aggregate_refs, aggregate_snapshots, strict=True):
                data = snapshot.to_dict() or {}
                reconciled, reserved, _, legacy = _usage_amounts(data, now)
                new_reserved = max(Decimal(0), reserved - amount)
                new_reconciled = reconciled + (actual_usd or Decimal(0))
                tx.set(
                    reference,
                    {
                        "reconciled_usd": str(new_reconciled),
                        "reserved_usd": str(new_reserved),
                        "reservations": legacy,
                        "updated_at": now,
                    },
                    merge=True,
                )
            tx.set(
                reservation_ref,
                {
                    "actual_usd": str(actual_usd) if actual_usd is not None else None,
                    "status": "RECONCILED" if actual_usd is not None else "RELEASED",
                    "expires_at": None,
                    "settled_at": now,
                },
                merge=True,
            )

        await self._run_transaction(finish, operation="finish")

    async def reconcile_async(self, reservation_id: str, *, actual_usd: Decimal) -> None:
        if actual_usd < 0:
            raise ValueError("actual cost cannot be negative")
        await self._finish(reservation_id, actual_usd=actual_usd)

    async def release_async(self, reservation_id: str) -> None:
        await self._finish(reservation_id, actual_usd=None)
