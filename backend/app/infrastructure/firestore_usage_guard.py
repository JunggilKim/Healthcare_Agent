from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from google.cloud import firestore

from backend.app.infrastructure.usage_guard import (
    CostGuardExceeded,
    CostReservation,
)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _active_reservations(data: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    reservations = data.get("reservations", {})
    return {
        key: value
        for key, value in reservations.items()
        if datetime.fromisoformat(str(value["expires_at"])) > now
    }


class FirestoreUsageGuard:
    """Cross-instance cost reservations using one transaction over session/day/total documents."""

    def __init__(
        self,
        client: firestore.AsyncClient,
        *,
        session_cap_usd: Decimal,
        daily_cap_usd: Decimal,
        total_cap_usd: Decimal,
        stale_after: timedelta = timedelta(minutes=20),
    ) -> None:
        self.client = client
        self.session_cap_usd = session_cap_usd
        self.daily_cap_usd = daily_cap_usd
        self.total_cap_usd = total_cap_usd
        self.stale_after = stale_after
        # Every reservation touches the shared total document. Serializing only
        # the short accounting transactions inside one Cloud Run process avoids
        # self-inflicted Firestore ABORTED conflicts while model calls still run
        # concurrently after their reservations have been acquired.
        self._transaction_lock = asyncio.Lock()

    @staticmethod
    def _projected(data: dict[str, Any], reservations: dict[str, dict[str, Any]]) -> Decimal:
        return _decimal(data.get("reconciled_usd")) + sum(
            (_decimal(item["amount_usd"]) for item in reservations.values()), Decimal(0)
        )

    async def reserve_async(self, *, session_id: str, amount_usd: Decimal) -> CostReservation:
        if amount_usd <= 0:
            raise ValueError("reservation amount must be positive")
        now = datetime.now(UTC)
        reservation = CostReservation(
            reservation_id=f"reservation_{uuid4()}",
            session_id=session_id,
            amount_usd=amount_usd,
            created_at=now,
        )
        transaction = self.client.transaction()
        session_ref = self.client.collection("session_usage").document(session_id)
        day_ref = self.client.collection("daily_usage").document(now.date().isoformat())
        total_ref = self.client.collection("usage_control").document("total")

        @firestore.async_transactional
        async def reserve(tx: Any) -> None:
            session_snapshot = await session_ref.get(transaction=tx)
            day_snapshot = await day_ref.get(transaction=tx)
            total_snapshot = await total_ref.get(transaction=tx)
            session_data = session_snapshot.to_dict() or {}
            day_data = day_snapshot.to_dict() or {}
            total_data = total_snapshot.to_dict() or {}
            session_active = _active_reservations(session_data, now)
            day_active = _active_reservations(day_data, now)
            total_active = _active_reservations(total_data, now)
            if self._projected(session_data, session_active) + amount_usd > self.session_cap_usd:
                raise CostGuardExceeded("SESSION_COST_CAP_EXCEEDED")
            if self._projected(day_data, day_active) + amount_usd > self.daily_cap_usd:
                raise CostGuardExceeded("DAILY_COST_CAP_EXCEEDED")
            if self._projected(total_data, total_active) + amount_usd > self.total_cap_usd:
                raise CostGuardExceeded("TOTAL_COST_CAP_EXCEEDED")
            entry = {
                "session_id": session_id,
                "amount_usd": str(amount_usd),
                "created_at": now.isoformat(),
                "expires_at": (now + self.stale_after).isoformat(),
            }
            for reference, data, active in (
                (session_ref, session_data, session_active),
                (day_ref, day_data, day_active),
                (total_ref, total_data, total_active),
            ):
                active[reservation.reservation_id] = entry
                tx.set(
                    reference,
                    {
                        "reconciled_usd": str(_decimal(data.get("reconciled_usd"))),
                        "reservations": active,
                        "updated_at": now,
                    },
                )

        async with self._transaction_lock:
            await reserve(transaction)
        return reservation

    async def _finish(self, reservation_id: str, *, actual_usd: Decimal | None) -> None:
        now = datetime.now(UTC)
        transaction = self.client.transaction()
        total_ref = self.client.collection("usage_control").document("total")

        @firestore.async_transactional
        async def finish(tx: Any) -> None:
            total_snapshot = await total_ref.get(transaction=tx)
            total_data = total_snapshot.to_dict() or {}
            total_active = _active_reservations(total_data, now)
            entry = total_active.get(reservation_id)
            if entry is None:
                return
            session_ref = self.client.collection("session_usage").document(entry["session_id"])
            created = datetime.fromisoformat(entry["created_at"])
            day_ref = self.client.collection("daily_usage").document(created.date().isoformat())
            session_snapshot = await session_ref.get(transaction=tx)
            day_snapshot = await day_ref.get(transaction=tx)
            session_data = session_snapshot.to_dict() or {}
            day_data = day_snapshot.to_dict() or {}
            for reference, data in (
                (session_ref, session_data),
                (day_ref, day_data),
                (total_ref, total_data),
            ):
                active = _active_reservations(data, now)
                active.pop(reservation_id, None)
                reconciled = _decimal(data.get("reconciled_usd"))
                if actual_usd is not None:
                    reconciled += actual_usd
                tx.set(
                    reference,
                    {
                        "reconciled_usd": str(reconciled),
                        "reservations": active,
                        "updated_at": now,
                    },
                )

        async with self._transaction_lock:
            await finish(transaction)

    async def reconcile_async(self, reservation_id: str, *, actual_usd: Decimal) -> None:
        if actual_usd < 0:
            raise ValueError("actual cost cannot be negative")
        await self._finish(reservation_id, actual_usd=actual_usd)

    async def release_async(self, reservation_id: str) -> None:
        await self._finish(reservation_id, actual_usd=None)
