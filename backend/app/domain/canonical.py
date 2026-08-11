from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import orjson
import yaml
from pydantic import BaseModel


def _canonical_default(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize canonical JSON as UTF-8 with sorted keys and no whitespace."""

    return orjson.dumps(value, default=_canonical_default, option=orjson.OPT_SORT_KEYS)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration must be a mapping: {path}")
    return loaded


def load_config_bundle(config_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all repository YAML configs in deterministic filename order."""

    return {path.stem: load_yaml(path) for path in sorted(config_dir.glob("*.yaml"))}
