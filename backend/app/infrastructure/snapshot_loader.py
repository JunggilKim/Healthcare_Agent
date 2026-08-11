from __future__ import annotations

import hashlib
from pathlib import Path

import orjson
from pydantic import Field

from backend.app.domain.base import StrictModel


class SnapshotFile(StrictModel):
    path: str
    sha256: str
    size_bytes: int = Field(ge=0)


class SnapshotCase(StrictModel):
    case_id: str
    complete: bool
    artifact_paths: list[str]


class SnapshotManifest(StrictModel):
    schema_version: str
    snapshot_version: str
    built_at: str
    data_timestamp: str
    cases: list[SnapshotCase]
    files: list[SnapshotFile]
    complete: bool


class SnapshotIntegrityError(ValueError):
    pass


def load_verified_snapshot(root: Path, *, require_complete: bool = True) -> SnapshotManifest:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotIntegrityError("SNAPSHOT_MANIFEST_MISSING")
    manifest = SnapshotManifest.model_validate(orjson.loads(manifest_path.read_bytes()))
    if require_complete and not manifest.complete:
        raise SnapshotIntegrityError("SNAPSHOT_INCOMPLETE")
    declared_paths = {item.path for item in manifest.files}
    referenced_paths = {
        artifact_path for case in manifest.cases for artifact_path in case.artifact_paths
    }
    if referenced_paths - declared_paths:
        raise SnapshotIntegrityError("SNAPSHOT_UNHASHED_REFERENCE")
    for entry in manifest.files:
        path = (root / entry.path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as error:
            raise SnapshotIntegrityError("SNAPSHOT_PATH_ESCAPE") from error
        if not path.is_file():
            raise SnapshotIntegrityError(f"SNAPSHOT_FILE_MISSING:{entry.path}")
        content = path.read_bytes()
        if len(content) != entry.size_bytes:
            raise SnapshotIntegrityError(f"SNAPSHOT_SIZE_MISMATCH:{entry.path}")
        if hashlib.sha256(content).hexdigest() != entry.sha256:
            raise SnapshotIntegrityError(f"SNAPSHOT_HASH_MISMATCH:{entry.path}")
    return manifest
