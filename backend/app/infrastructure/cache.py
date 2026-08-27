from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from google.cloud import firestore
from pydantic import ValidationError

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.generation import StructuredGenerationRecord


def model_cache_key(
    *,
    model_id: str,
    task_name: str,
    prompt_version: str,
    output_schema_version: str,
    slot_catalog_version: str,
    normalized_input: object,
    generation_config: object,
) -> str:
    parts = [
        model_id,
        task_name,
        prompt_version,
        output_schema_version,
        slot_catalog_version,
        canonical_json_bytes(normalized_input).decode(),
        canonical_json_bytes(generation_config).decode(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class ModelResultCache(Protocol):
    async def get(self, key: str) -> StructuredGenerationRecord | None: ...

    async def put(self, record: StructuredGenerationRecord) -> str: ...


class LocalModelResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _get(self, key: str) -> StructuredGenerationRecord | None:
        path = self.root / "llm" / f"{key}.json"
        if not path.is_file():
            return None
        try:
            record = StructuredGenerationRecord.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            return None
        return record if record.cache_key == key else None

    async def get(self, key: str) -> StructuredGenerationRecord | None:
        return await asyncio.to_thread(self._get, key)

    def _put(self, record: StructuredGenerationRecord) -> str:
        path = self.root / "llm" / f"{record.cache_key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(canonical_json_bytes(record))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return str(path)

    async def put(self, record: StructuredGenerationRecord) -> str:
        return await asyncio.to_thread(self._put, record)


class FirestoreModelResultCache:
    """Shared immutable small-result cache for Cloud Run instances."""

    def __init__(self, client: firestore.AsyncClient) -> None:
        self._collection = client.collection("llm_cache")

    async def get(self, key: str) -> StructuredGenerationRecord | None:
        snapshot = await self._collection.document(key).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        return StructuredGenerationRecord.model_validate(data["record"])

    async def put(self, record: StructuredGenerationRecord) -> str:
        reference = self._collection.document(record.cache_key)
        await reference.set(
            {
                "record": record.model_dump(mode="json"),
                "model_id": record.model_id,
                "task_name": record.task_name,
                "created_at": record.usage.created_at,
            },
            merge=False,
        )
        return str(reference.path)
