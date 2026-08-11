from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from google.cloud import firestore

RateLimitKind = Literal["snapshot_session", "live_session", "cold_compile", "answer_submission"]

_LIMITS: dict[RateLimitKind, int] = {
    "snapshot_session": 20,
    "live_session": 5,
    "cold_compile": 2,
    "answer_submission": 30,
}


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime
    subject_hash: str


class FixedWindowRateLimiter:
    def __init__(
        self,
        *,
        salt: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not salt:
            raise ValueError("rate-limit salt must not be empty")
        self._salt = salt
        self._now = now
        self._counters: dict[tuple[str, RateLimitKind, datetime], int] = {}
        self._lock = threading.Lock()

    def _subject_hash(self, client_ip: str) -> str:
        return hashlib.sha256(f"{self._salt}:{client_ip}".encode()).hexdigest()

    def consume(self, client_ip: str, kind: RateLimitKind) -> RateLimitResult:
        now = self._now().astimezone(UTC)
        window = now.replace(minute=0, second=0, microsecond=0)
        reset_at = window + timedelta(hours=1)
        subject_hash = self._subject_hash(client_ip)
        key = (subject_hash, kind, window)
        limit = _LIMITS[kind]
        with self._lock:
            current = self._counters.get(key, 0)
            allowed = current < limit
            if allowed:
                current += 1
                self._counters[key] = current
            self._counters = {
                counter_key: value
                for counter_key, value in self._counters.items()
                if counter_key[2] >= window
            }
        return RateLimitResult(
            allowed=allowed,
            remaining=max(0, limit - current),
            reset_at=reset_at,
            subject_hash=subject_hash,
        )

    def stored_subjects(self) -> set[str]:
        with self._lock:
            return {key[0] for key in self._counters}

    async def consume_async(self, client_ip: str, kind: RateLimitKind) -> RateLimitResult:
        return self.consume(client_ip, kind)


class FirestoreFixedWindowRateLimiter:
    """Cross-instance hourly counters that persist only a salted IP hash."""

    def __init__(
        self,
        client: firestore.AsyncClient,
        *,
        salt: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not salt:
            raise ValueError("rate-limit salt must not be empty")
        self._client = client
        self._salt = salt
        self._now = now

    def _subject_hash(self, client_ip: str) -> str:
        return hashlib.sha256(f"{self._salt}:{client_ip}".encode()).hexdigest()

    async def consume_async(self, client_ip: str, kind: RateLimitKind) -> RateLimitResult:
        now = self._now().astimezone(UTC)
        window = now.replace(minute=0, second=0, microsecond=0)
        reset_at = window + timedelta(hours=1)
        subject_hash = self._subject_hash(client_ip)
        limit = _LIMITS[kind]
        document_id = f"{window:%Y%m%d%H}-{kind}-{subject_hash}"
        reference = self._client.collection("rate_limits").document(document_id)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def increment(tx: Any) -> tuple[bool, int]:
            snapshot = await reference.get(transaction=tx)
            current = int((snapshot.to_dict() or {}).get("count", 0))
            allowed = current < limit
            if allowed:
                current += 1
                tx.set(
                    reference,
                    {
                        "subject_hash": subject_hash,
                        "kind": kind,
                        "window_start": window,
                        "reset_at": reset_at,
                        "count": current,
                        "updated_at": now,
                    },
                )
            return allowed, current

        allowed, current = await increment(transaction)
        return RateLimitResult(
            allowed=allowed,
            remaining=max(0, limit - current),
            reset_at=reset_at,
            subject_hash=subject_hash,
        )
