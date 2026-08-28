from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import orjson


class ArtifactCorruptionError(RuntimeError):
    pass


class LocalArtifactStore:
    """Content-addressed byte storage used by live cache and bundled snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, namespace: str, content: bytes, *, suffix: str = ".json") -> tuple[str, Path]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / namespace / f"{digest}{suffix}"
        if not path.exists():
            self._atomic_write(path, content)
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
        self._atomic_write(path, orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
        return path

    def read_reference(self, namespace: str, key: str) -> dict[str, object] | None:
        path = self.root / namespace / f"{key}.json"
        if not path.is_file():
            return None
        try:
            value = orjson.loads(path.read_bytes())
        except (OSError, orjson.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
