from __future__ import annotations

import hashlib
from pathlib import Path

import orjson


class ArtifactCorruptionError(RuntimeError):
    pass


class LocalArtifactStore:
    """Content-addressed byte storage used by live cache and bundled snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, namespace: str, content: bytes, *, suffix: str = ".json") -> tuple[str, Path]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / namespace / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return digest, path

    def read_verified(self, path: Path, expected_sha256: str) -> bytes:
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            raise ArtifactCorruptionError(
                f"artifact hash mismatch: expected {expected_sha256}, found {actual}"
            )
        return content

    def put_reference(self, namespace: str, key: str, payload: dict[str, object]) -> Path:
        path = self.root / namespace / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
        return path

    def read_reference(self, namespace: str, key: str) -> dict[str, object] | None:
        path = self.root / namespace / f"{key}.json"
        if not path.exists():
            return None
        value = orjson.loads(path.read_bytes())
        return value if isinstance(value, dict) else None
