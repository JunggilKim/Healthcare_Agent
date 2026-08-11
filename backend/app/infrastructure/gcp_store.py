from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import orjson
from google.api_core.exceptions import PreconditionFailed
from google.cloud import firestore
from google.cloud.storage import Client as StorageClient  # type: ignore[import-untyped]

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.events import SessionEvent
from backend.app.domain.sessions import SessionState


class GcpSessionStore:
    """Firestore metadata/events plus content-addressed GCS artifacts.

    The attached Cloud Run service account supplies ADC. No key file or secret value is logged.
    """

    def __init__(self, *, project: str, database: str, bucket_name: str, hmac_salt: str) -> None:
        if not project or not bucket_name:
            raise ValueError("GCP store requires GOOGLE_CLOUD_PROJECT and GCS_BUCKET")
        if not hmac_salt or hmac_salt == "local-development-only-change-me":
            raise ValueError("GCP store requires SESSION_TOKEN_HMAC_SALT")
        self.firestore = firestore.AsyncClient(project=project, database=database)
        self.storage = StorageClient(project=project)
        self.bucket = self.storage.bucket(bucket_name)
        self._hmac_key = hmac_salt.encode("utf-8")

    async def initialize(self) -> None:
        # Client construction validates configuration without adding a startup network dependency.
        return None

    def token_hash(self, token: str) -> str:
        return hmac.new(self._hmac_key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def _session_ref(self, session_id: str) -> Any:
        return self.firestore.collection("sessions").document(session_id)

    async def _payload_fields(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        content = canonical_json_bytes(payload)
        digest = hashlib.sha256(content).hexdigest()
        if len(content) < 750_000:
            return {
                "payload_json": content,
                "payload_gcs_uri": None,
                "payload_sha256": digest,
                "payload_size_bytes": len(content),
            }
        object_name = f"sessions/{session_id}/state/{digest}.json"
        blob = self.bucket.blob(object_name)

        def upload() -> None:
            try:
                blob.upload_from_string(
                    content,
                    content_type="application/json",
                    if_generation_match=0,
                )
            except PreconditionFailed:
                return

        await asyncio.to_thread(upload)
        return {
            "payload_json": None,
            "payload_gcs_uri": f"gs://{self.bucket.name}/{object_name}",
            "payload_sha256": digest,
            "payload_size_bytes": len(content),
        }

    async def _read_payload(self, values: dict[str, Any]) -> dict[str, Any]:
        inline = values.get("payload_json")
        if inline is not None:
            content = bytes(inline)
        else:
            uri = str(values.get("payload_gcs_uri", ""))
            prefix = f"gs://{self.bucket.name}/"
            if not uri.startswith(prefix):
                raise ValueError("session payload GCS URI is outside the configured bucket")
            object_name = uri.removeprefix(prefix)
            content = await asyncio.to_thread(self.bucket.blob(object_name).download_as_bytes)
        expected = str(values.get("payload_sha256", ""))
        if not expected or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected):
            raise ValueError("session payload hash mismatch")
        return cast(dict[str, Any], orjson.loads(content))

    async def create_session(self, session_id: str, token: str, payload: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        payload_fields = await self._payload_fields(session_id, payload)
        await self._session_ref(session_id).create(
            {
                "token_hash": self.token_hash(token),
                "state": SessionState.CREATED.value,
                "patient_state_version": 0,
                **payload_fields,
                "deleted": False,
                "created_at": now,
                "updated_at": now,
                "expires_at": expires_at,
            }
        )

    async def authenticate(self, session_id: str, token: str) -> bool:
        snapshot = await self._session_ref(session_id).get(field_paths=["token_hash", "deleted"])
        if not snapshot.exists:
            return False
        values = snapshot.to_dict() or {}
        return not values.get("deleted", False) and hmac.compare_digest(
            str(values.get("token_hash", "")), self.token_hash(token)
        )

    async def read_session(self, session_id: str) -> dict[str, Any] | None:
        snapshot = await self._session_ref(session_id).get()
        if not snapshot.exists:
            return None
        values = snapshot.to_dict() or {}
        if values.get("deleted", False):
            return None
        payload = await self._read_payload(values)
        payload.update(
            {
                "state": values["state"],
                "patient_state_version": values["patient_state_version"],
                "created_at": values["created_at"].isoformat(),
                "updated_at": values["updated_at"].isoformat(),
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
        transaction = self.firestore.transaction()
        session_ref = self._session_ref(session_id)
        payload_fields = await self._payload_fields(session_id, session_payload)

        @firestore.async_transactional
        async def update(tx: Any) -> SessionEvent:
            snapshot = await session_ref.get(transaction=tx)
            if not snapshot.exists:
                raise KeyError(session_id)
            values = snapshot.to_dict() or {}
            if values.get("deleted", False):
                raise KeyError(session_id)
            if values["state"] != expected_state.value:
                raise ValueError(
                    f"session state changed: expected {expected_state.value}, "
                    f"found {values['state']}"
                )
            sequence = int(values.get("event_sequence", 0)) + 1
            now = datetime.now(UTC)
            event = SessionEvent(
                event_id=f"evt_{uuid4()}",
                session_id=session_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=now,
            )
            event_ref = session_ref.collection("events").document(f"{sequence:010d}")
            tx.set(
                event_ref,
                {
                    "event_id": event.event_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "payload_json": canonical_json_bytes(payload),
                    "created_at": now,
                },
            )
            tx.update(
                session_ref,
                {
                    "state": target_state.value,
                    "patient_state_version": patient_state_version,
                    **payload_fields,
                    "updated_at": now,
                    "event_sequence": sequence,
                },
            )
            return event

        return await update(transaction)

    async def append_event_without_transition(
        self, *, session_id: str, event_type: str, payload: dict[str, Any]
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
        query = self._session_ref(session_id).collection("events").order_by("sequence")
        events: list[SessionEvent] = []
        async for snapshot in query.stream():
            values = snapshot.to_dict() or {}
            events.append(
                SessionEvent(
                    event_id=values["event_id"],
                    session_id=session_id,
                    sequence=values["sequence"],
                    event_type=values["event_type"],
                    payload=orjson.loads(values["payload_json"]),
                    created_at=values["created_at"],
                )
            )
        return events

    async def write_json_artifact(self, namespace: str, payload: object) -> tuple[str, str]:
        content = canonical_json_bytes(payload)
        digest = hashlib.sha256(content).hexdigest()
        safe_namespace = namespace.replace("..", "_").strip("/")
        object_name = f"{safe_namespace}/{digest}.json"
        blob = self.bucket.blob(object_name)

        def upload_if_absent() -> None:
            try:
                blob.upload_from_string(
                    content, content_type="application/json", if_generation_match=0
                )
            except PreconditionFailed:
                return

        await asyncio.to_thread(upload_if_absent)
        return f"gs://{self.bucket.name}/{object_name}", digest

    async def delete_session(self, session_id: str) -> bool:
        """Hide a session and queue best-effort GCS cleanup in one transaction."""

        transaction = self.firestore.transaction()
        session_ref = self._session_ref(session_id)
        cleanup_ref = self.firestore.collection("cleanup_requests").document(session_id)

        @firestore.async_transactional
        async def mark_deleted(tx: Any) -> bool:
            snapshot = await session_ref.get(transaction=tx)
            if not snapshot.exists:
                return False
            values = snapshot.to_dict() or {}
            if values.get("deleted", False):
                return True
            now = datetime.now(UTC)
            sequence = int(values.get("event_sequence", 0)) + 1
            event_ref = session_ref.collection("events").document(f"{sequence:010d}")
            tx.set(
                event_ref,
                {
                    "event_id": f"evt_{uuid4()}",
                    "sequence": sequence,
                    "event_type": "SESSION_DELETED",
                    "payload_json": canonical_json_bytes({"cleanup_queued": True}),
                    "created_at": now,
                },
            )
            tx.update(
                session_ref,
                {
                    "deleted": True,
                    "updated_at": now,
                    "event_sequence": sequence,
                },
            )
            tx.set(
                cleanup_ref,
                {
                    "session_id": session_id,
                    "prefix": f"sessions/{session_id}/",
                    "status": "QUEUED",
                    "created_at": now,
                },
            )
            return True

        return await mark_deleted(transaction)

    async def acquire_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        transaction = self.firestore.transaction()
        reference = self._session_ref(session_id)

        @firestore.async_transactional
        async def acquire(tx: Any) -> bool:
            snapshot = await reference.get(transaction=tx)
            if not snapshot.exists or (snapshot.to_dict() or {}).get("deleted", False):
                raise KeyError(session_id)
            values = snapshot.to_dict() or {}
            now = datetime.now(UTC)
            current_owner = values.get("lease_owner_id")
            expires_at = values.get("lease_expires_at")
            if (
                current_owner not in {None, owner_id}
                and expires_at is not None
                and expires_at > now
            ):
                return False
            tx.update(
                reference,
                {
                    "lease_owner_id": owner_id,
                    "lease_expires_at": now + duration,
                    "lease_updated_at": now,
                },
            )
            return True

        return await acquire(transaction)

    async def renew_lease(self, session_id: str, owner_id: str, *, duration: timedelta) -> bool:
        transaction = self.firestore.transaction()
        reference = self._session_ref(session_id)

        @firestore.async_transactional
        async def renew(tx: Any) -> bool:
            snapshot = await reference.get(transaction=tx)
            if not snapshot.exists:
                return False
            values = snapshot.to_dict() or {}
            if values.get("lease_owner_id") != owner_id:
                return False
            now = datetime.now(UTC)
            tx.update(
                reference,
                {"lease_expires_at": now + duration, "lease_updated_at": now},
            )
            return True

        return await renew(transaction)

    async def release_lease(self, session_id: str, owner_id: str) -> None:
        transaction = self.firestore.transaction()
        reference = self._session_ref(session_id)

        @firestore.async_transactional
        async def release(tx: Any) -> None:
            snapshot = await reference.get(transaction=tx)
            if snapshot.exists and (snapshot.to_dict() or {}).get("lease_owner_id") == owner_id:
                tx.update(
                    reference,
                    {"lease_owner_id": None, "lease_expires_at": None},
                )

        await release(transaction)

    async def begin_answer_idempotency(
        self, session_id: str, key_hash: str
    ) -> tuple[str, list[dict[str, Any]] | None]:
        transaction = self.firestore.transaction()
        reference = self._session_ref(session_id).collection("idempotency").document(key_hash)

        @firestore.async_transactional
        async def begin(tx: Any) -> tuple[str, list[dict[str, Any]] | None]:
            snapshot = await reference.get(transaction=tx)
            if snapshot.exists:
                values = snapshot.to_dict() or {}
                response = values.get("response")
                return str(values["status"]), cast(list[dict[str, Any]] | None, response)
            now = datetime.now(UTC)
            tx.create(
                reference,
                {"status": "IN_PROGRESS", "response": None, "created_at": now, "updated_at": now},
            )
            return "NEW", None

        return await begin(transaction)

    async def complete_answer_idempotency(
        self, session_id: str, key_hash: str, response: list[dict[str, Any]]
    ) -> None:
        await (
            self._session_ref(session_id)
            .collection("idempotency")
            .document(key_hash)
            .update({"status": "COMPLETED", "response": response, "updated_at": datetime.now(UTC)})
        )

    async def abandon_answer_idempotency(self, session_id: str, key_hash: str) -> None:
        reference = self._session_ref(session_id).collection("idempotency").document(key_hash)
        snapshot = await reference.get()
        if snapshot.exists and (snapshot.to_dict() or {}).get("status") == "IN_PROGRESS":
            await reference.delete()
