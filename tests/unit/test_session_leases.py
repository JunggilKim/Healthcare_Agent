from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from backend.app.infrastructure.local_store import LocalSessionStore


@pytest.mark.asyncio
async def test_local_orchestration_lease_is_exclusive_and_releasable(tmp_path) -> None:
    store = LocalSessionStore(tmp_path / "store", hmac_salt="test-salt")
    await store.initialize()
    await store.create_session(
        "session-1",
        "token",
        {"expires_at": "2026-08-19T00:00:00+00:00", "mode": "snapshot"},
    )
    duration = timedelta(minutes=6)
    assert await store.acquire_lease("session-1", "owner-a", duration=duration)
    assert not await store.acquire_lease("session-1", "owner-b", duration=duration)
    assert await store.renew_lease("session-1", "owner-a", duration=duration)
    await store.release_lease("session-1", "owner-a")
    assert await store.acquire_lease("session-1", "owner-b", duration=duration)


@pytest.mark.asyncio
async def test_local_session_token_hash_survives_store_reconstruction(tmp_path) -> None:
    root = tmp_path / "store"
    first = LocalSessionStore(root, hmac_salt="stable-test-salt")
    await first.initialize()
    await first.create_session(
        "session-1",
        "secret-token",
        {"expires_at": "2026-08-19T00:00:00+00:00", "mode": "snapshot"},
    )
    second = LocalSessionStore(root, hmac_salt="stable-test-salt")
    await second.initialize()
    assert await second.authenticate("session-1", "secret-token")
    assert not await second.authenticate("session-1", "wrong-token")


@pytest.mark.asyncio
async def test_stale_orchestration_lease_can_be_reacquired(tmp_path) -> None:
    store = LocalSessionStore(tmp_path / "store", hmac_salt="test-salt")
    await store.initialize()
    await store.create_session(
        "session-1",
        "token",
        {"expires_at": "2026-08-19T00:00:00+00:00", "mode": "snapshot"},
    )
    assert await store.acquire_lease("session-1", "owner-stale", duration=timedelta(minutes=6))
    async with aiosqlite.connect(store.database_path) as database:
        await database.execute(
            "UPDATE orchestration_leases SET expires_at = ? WHERE session_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "session-1"),
        )
        await database.commit()

    assert await store.acquire_lease("session-1", "owner-recovery", duration=timedelta(minutes=6))
