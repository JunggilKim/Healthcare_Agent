from __future__ import annotations

import hashlib
from pathlib import Path

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


class LocalModelResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, key: str) -> StructuredGenerationRecord | None:
        path = self.root / "llm" / f"{key}.json"
        if not path.exists():
            return None
        return StructuredGenerationRecord.model_validate_json(path.read_bytes())

    def put(self, record: StructuredGenerationRecord) -> Path:
        path = self.root / "llm" / f"{record.cache_key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(record))
        return path
