from __future__ import annotations

import hashlib
from pathlib import Path

import orjson
import pytest

from backend.app.infrastructure.snapshot_loader import (
    SnapshotIntegrityError,
    load_verified_snapshot,
)


def _snapshot(root: Path) -> Path:
    root.mkdir()
    artifact = root / "sessions/S004/initial.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"{}")
    manifest = {
        "schema_version": "trial-opt-snapshot-v1",
        "snapshot_version": "test-v1",
        "built_at": "2026-08-11T09:00:00Z",
        "data_timestamp": "2026-08-11T09:00:00Z",
        "cases": [
            {
                "case_id": "S004",
                "complete": True,
                "artifact_paths": ["sessions/S004/initial.json"],
            }
        ],
        "files": [
            {
                "path": "sessions/S004/initial.json",
                "sha256": hashlib.sha256(b"{}").hexdigest(),
                "size_bytes": 2,
            }
        ],
        "complete": True,
    }
    (root / "manifest.json").write_bytes(orjson.dumps(manifest))
    return root


def test_snapshot_loader_verifies_every_reference(tmp_path: Path) -> None:
    manifest = load_verified_snapshot(_snapshot(tmp_path / "snapshot"))
    assert manifest.snapshot_version == "test-v1"


def test_snapshot_loader_rejects_tampering(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    (root / "sessions/S004/initial.json").write_text("changed", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError, match="SNAPSHOT_SIZE_MISMATCH"):
        load_verified_snapshot(root)
