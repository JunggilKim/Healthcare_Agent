from __future__ import annotations

import hashlib
from pathlib import Path

import orjson
from pydantic import BaseModel, ConfigDict

from backend.app.infrastructure.local_artifacts import ArtifactCorruptionError


class SnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str


class RetrievalSnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_version: str
    api_version: str
    data_timestamp: str
    case_id: str
    search_response: SnapshotEntry


def load_retrieval_snapshot(root: Path) -> tuple[RetrievalSnapshotManifest, bytes]:
    manifest_path = root / "manifest.json"
    manifest = RetrievalSnapshotManifest.model_validate(orjson.loads(manifest_path.read_bytes()))
    response_path = root / manifest.search_response.path
    response = response_path.read_bytes()
    actual = hashlib.sha256(response).hexdigest()
    if actual != manifest.search_response.sha256:
        raise ArtifactCorruptionError(
            f"snapshot hash mismatch: expected {manifest.search_response.sha256}, found {actual}"
        )
    return manifest, response
