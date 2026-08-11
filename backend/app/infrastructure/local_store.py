from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import aiosqlite
import orjson

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.events import SessionEvent
from backend.app.domain.sessions import SessionState


class LocalSessionStore:
    """SQLite metadata/event adapter with content-addressed local JSON objects."""

    def __init__(self, root: Path, *, hmac_salt: str | None = None) -> None:
        self.root = root
        self.database_path = root / "trial_opt.db"
        self.object_dir = root / "objects"
        self._process_hmac_key = (
            hmac_salt.encode("utf-8") if hmac_salt is not None else secrets.token_bytes(32)
        )

    async def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.object_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    patient_state_version INTEGER NOT NULL,
                    payload_json BLOB NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS events_session_sequence
                ON events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS orchestration_leases (
                    session_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_idempotency (
                    session_id TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json BLOB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, key_hash)
                );
                """
            )
            await database.commit()

    def token_hash(self, token: str) -> str:
        return hmac.new(self._process_hmac_key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    async def create_session(
        self,
        session_id: str,
        token: str,
        payload: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute(
                """
                INSERT INTO sessions(
                    session_id, token_hash, state, patient_state_version,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    self.token_hash(token),
                    SessionState.CREATED.value,
                    0,
                    canonical_json_bytes(payload),
                    now,
                    now,
                ),
            )
            await database.commit()

    async def authenticate(self, session_id: str, token: str) -> bool:
        async with aiosqlite.connect(self.database_path) as database:
            cursor = await database.execute(
                "SELECT token_hash FROM sessions WHERE session_id = ? AND deleted = 0",
                (session_id,),
            )
            row = await cursor.fetchone()
        return row is not None and hmac.compare_digest(str(row[0]), self.token_hash(token))

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.database_path) as database:
            cursor = await database.execute(
                """
                SELECT state, patient_state_version, payload_json, created_at, updated_at
                FROM sessions WHERE session_id = ? AND deleted = 0
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        payload = cast(dict[str, Any], orjson.loads(row[2]))
        payload.update(
            {
                "state": row[0],
                "patient_state_version": row[1],
                "created_at": row[3],
                "updated_at": row[4],
            }
        )
        return payload

    async def transition_and_append(
        self,
        *,
        session_id: str,
        expected_state: SessionState,
        target_state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        session_payload: dict[str, Any],
        patient_state_version: int,
    ) -> SessionEvent:
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.database_path, isolation_level=None) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "SELECT state FROM sessions WHERE session_id = ? AND deleted = 0",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await database.rollback()
                raise KeyError(session_id)
            if row[0] != expected_state.value:
                await database.rollback()
                raise ValueError(
                    f"session state changed: expected {expected_state.value}, found {row[0]}"
                )
            cursor = await database.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE session_id = ?",
                (session_id,),
            )
            sequence_row = await cursor.fetchone()
            if sequence_row is None:
                await database.rollback()
                raise RuntimeError("event sequence query returned no row")
            sequence = int(sequence_row[0])
            event = SessionEvent(
                event_id=f"evt_{uuid4()}",
                session_id=session_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=now,
            )
            await database.execute(
                """
                INSERT INTO events(
                    session_id, sequence, event_id, event_type, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sequence,
                    event.event_id,
                    event_type,
                    canonical_json_bytes(payload),
                    now.isoformat(),
                ),
            )
            await database.execute(
                """
                UPDATE sessions
                SET state = ?, patient_state_version = ?, payload_json = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    target_state.value,
                    patient_state_version,
                    canonical_json_bytes(session_payload),
                    now.isoformat(),
                    session_id,
                ),
            )
            await database.commit()
        return event

    async def append_event_without_transition(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> SessionEvent:
        session = await self.read_session(session_id)
        if session is None:
            raise KeyError(session_id)
        state = SessionState(session["state"])
        return await self.transition_and_append(
            session_id=session_id,
            expected_state=state,
            target_state=state,
            event_type=event_type,
            payload=payload,
            session_payload={
                key: value
                for key, value in session.items()
                if key not in {"state", "patient_state_version", "created_at", "updated_at"}
            },
            patient_state_version=int(session["patient_state_version"]),
        )

    async def list_events(self, session_id: str) -> list[SessionEvent]:
        async with aiosqlite.connect(self.database_path) as database:
            cursor = await database.execute(
                """
                SELECT event_id, sequence, event_type, payload_json, created_at
                FROM events WHERE session_id = ? ORDER BY sequence ASC
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [
            SessionEvent(
                event_id=row[0],
                session_id=session_id,
                sequence=row[1],
                event_type=row[2],
                payload=orjson.loads(row[3]),
                created_at=row[4],
            )
            for row in rows
        ]

    async def write_json_artifact(self, namespace: str, payload: object) -> tuple[str, str]:
        content = canonical_json_bytes(payload)
        digest = hashlib.sha256(content).hexdigest()
        safe_namespace = namespace.replace("..", "_").strip("/")
        target_dir = self.object_dir / safe_namespace
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}.json"
        if not target.exists():
            temporary = target.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_bytes(content)
            temporary.replace(target)
        return str(target), digest

    async def delete_session(self, session_id: str) -> bool:
        """Soft-delete atomically so authentication and normal reads stop immediately."""

        now = datetime.now(UTC)
        async with aiosqlite.connect(self.database_path, isolation_level=None) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "SELECT deleted FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                await database.rollback()
                return False
            if int(row[0]) == 1:
                await database.commit()
                return True
            cursor = await database.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE session_id = ?",
                (session_id,),
            )
            sequence_row = await cursor.fetchone()
            assert sequence_row is not None
            sequence = int(sequence_row[0])
            await database.execute(
                """
                INSERT INTO events(
                    session_id, sequence, event_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sequence,
                    f"evt_{uuid4()}",
                    "SESSION_DELETED",
                    canonical_json_bytes({"cleanup_queued": True}),
                    now.isoformat(),
                ),
            )
            await database.execute(
                "UPDATE sessions SET deleted = 1, updated_at = ? WHERE session_id = ?",
                (now.isoformat(), session_id),
            )
            await database.commit()
        session_objects = (self.object_dir / "sessions" / session_id).resolve()
        if self.object_dir.resolve() in session_objects.parents and session_objects.is_dir():
            shutil.rmtree(session_objects)
        return True

    async def acquire_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        now = datetime.now(UTC)
        expires_at = now + duration
        async with aiosqlite.connect(self.database_path, isolation_level=None) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "SELECT 1 FROM sessions WHERE session_id = ? AND deleted = 0", (session_id,)
            )
            if await cursor.fetchone() is None:
                await database.rollback()
                raise KeyError(session_id)
            cursor = await database.execute(
                "SELECT owner_id, expires_at FROM orchestration_leases WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is not None and row[0] != owner_id and datetime.fromisoformat(row[1]) > now:
                await database.rollback()
                return False
            await database.execute(
                """
                INSERT INTO orchestration_leases(session_id, owner_id, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    expires_at = excluded.expires_at
                """,
                (session_id, owner_id, expires_at.isoformat()),
            )
            await database.commit()
        return True

    async def renew_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        expires_at = datetime.now(UTC) + duration
        async with aiosqlite.connect(self.database_path) as database:
            cursor = await database.execute(
                """
                UPDATE orchestration_leases SET expires_at = ?
                WHERE session_id = ? AND owner_id = ?
                """,
                (expires_at.isoformat(), session_id, owner_id),
            )
            await database.commit()
        return cursor.rowcount == 1

    async def release_lease(self, session_id: str, owner_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute(
                "DELETE FROM orchestration_leases WHERE session_id = ? AND owner_id = ?",
                (session_id, owner_id),
            )
            await database.commit()

    async def begin_answer_idempotency(
        self, session_id: str, key_hash: str
    ) -> tuple[str, list[dict[str, Any]] | None]:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.database_path, isolation_level=None) as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                """
                INSERT OR IGNORE INTO answer_idempotency(
                    session_id, key_hash, status, response_json, created_at, updated_at
                ) VALUES (?, ?, 'IN_PROGRESS', NULL, ?, ?)
                """,
                (session_id, key_hash, now, now),
            )
            if cursor.rowcount == 1:
                await database.commit()
                return "NEW", None
            cursor = await database.execute(
                """
                SELECT status, response_json FROM answer_idempotency
                WHERE session_id = ? AND key_hash = ?
                """,
                (session_id, key_hash),
            )
            row = await cursor.fetchone()
            await database.commit()
        assert row is not None
        response = cast(list[dict[str, Any]] | None, orjson.loads(row[1]) if row[1] else None)
        return str(row[0]), response

    async def complete_answer_idempotency(
        self, session_id: str, key_hash: str, response: list[dict[str, Any]]
    ) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute(
                """
                UPDATE answer_idempotency
                SET status = 'COMPLETED', response_json = ?, updated_at = ?
                WHERE session_id = ? AND key_hash = ? AND status = 'IN_PROGRESS'
                """,
                (
                    canonical_json_bytes(response),
                    datetime.now(UTC).isoformat(),
                    session_id,
                    key_hash,
                ),
            )
            await database.commit()

    async def abandon_answer_idempotency(self, session_id: str, key_hash: str) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await database.execute(
                """
                DELETE FROM answer_idempotency
                WHERE session_id = ? AND key_hash = ? AND status = 'IN_PROGRESS'
                """,
                (session_id, key_hash),
            )
            await database.commit()
