from __future__ import annotations

import time
from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest
from google.api_core.exceptions import Aborted

import backend.app.infrastructure.firestore_usage_guard as usage_module
from backend.app.infrastructure.firestore_usage_guard import FirestoreUsageGuard
from backend.app.infrastructure.usage_guard import CostGuardExceeded


class _Snapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self.exists = data is not None
        self._data = deepcopy(data)

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._data)


class _Reference:
    def __init__(self, documents: dict[str, dict[str, Any]], path: str) -> None:
        self.documents = documents
        self.path = path

    async def get(self, **_: object) -> _Snapshot:
        return _Snapshot(self.documents.get(self.path))


class _Collection:
    def __init__(self, documents: dict[str, dict[str, Any]], name: str) -> None:
        self.documents = documents
        self.name = name

    def document(self, document_id: str) -> _Reference:
        return _Reference(self.documents, f"{self.name}/{document_id}")


class _Transaction:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents

    def set(self, reference: _Reference, values: dict[str, Any], *, merge: bool = False) -> None:
        current = self.documents.get(reference.path, {}) if merge else {}
        self.documents[reference.path] = {**current, **deepcopy(values)}

    def create(self, reference: _Reference, values: dict[str, Any]) -> None:
        if reference.path in self.documents:
            raise RuntimeError("already exists")
        self.documents[reference.path] = deepcopy(values)


class _Client:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self.documents, name)

    def transaction(self, **_: object) -> _Transaction:
        return _Transaction(self.documents)


@pytest.fixture(autouse=True)
def _direct_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        usage_module.firestore,
        "async_transactional",
        lambda callback: lambda transaction: callback(transaction),
    )


def _guard(client: _Client, **kwargs: object) -> FirestoreUsageGuard:
    guard = FirestoreUsageGuard(
        client,  # type: ignore[arg-type]
        session_cap_usd=Decimal("1.00"),
        daily_cap_usd=Decimal("2.00"),
        total_cap_usd=Decimal("3.00"),
        cleanup_interval_seconds=60,
        **kwargs,
    )
    guard._last_cleanup_monotonic = time.monotonic()
    return guard


@pytest.mark.asyncio
async def test_reservation_ledger_reconcile_is_idempotent() -> None:
    client = _Client()
    guard = _guard(client)

    reservation = await guard.reserve_async(session_id="session-one", amount_usd=Decimal("0.40"))
    assert client.documents["usage_control/total"]["reserved_usd"] == "0.40"
    ledger_path = f"usage_reservations/{reservation.reservation_id}"
    assert client.documents[ledger_path]["status"] == "RESERVED"

    await guard.reconcile_async(reservation.reservation_id, actual_usd=Decimal("0.25"))
    await guard.reconcile_async(reservation.reservation_id, actual_usd=Decimal("0.25"))

    assert client.documents["usage_control/total"]["reserved_usd"] == "0"
    assert client.documents["usage_control/total"]["reconciled_usd"] == "0.25"
    assert client.documents[ledger_path]["status"] == "RECONCILED"
    assert client.documents[ledger_path]["actual_usd"] == "0.25"


@pytest.mark.asyncio
async def test_distributed_aggregate_cap_blocks_second_guard_instance() -> None:
    client = _Client()
    first = _guard(client)
    second = _guard(client)

    await first.reserve_async(session_id="same-session", amount_usd=Decimal("0.75"))
    with pytest.raises(CostGuardExceeded, match="SESSION_COST_CAP_EXCEEDED"):
        await second.reserve_async(session_id="same-session", amount_usd=Decimal("0.30"))


@pytest.mark.asyncio
async def test_outer_transaction_retry_recovers_cross_instance_abort() -> None:
    client = _Client()
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    guard = _guard(client, sleep=sleep, jitter=lambda _a, _b: 0.0)
    attempts = 0

    async def operation(_: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Aborted("contention")
        return "committed"

    result = await guard._run_transaction(operation, operation="test")

    assert result == "committed"
    assert attempts == 3
    assert sleeps == [0.05, 0.1]
