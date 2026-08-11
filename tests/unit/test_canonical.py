from __future__ import annotations

from decimal import Decimal

from backend.app.domain.canonical import canonical_json_bytes, canonical_sha256
from backend.app.settings import REPOSITORY_ROOT, config_bundle_hash, get_config_bundle


def test_canonical_json_is_order_independent_and_decimal_safe() -> None:
    left = {"b": Decimal("1.20"), "a": [True, None]}
    right = {"a": [True, None], "b": Decimal("1.20")}
    assert canonical_json_bytes(left) == b'{"a":[true,null],"b":"1.20"}'
    assert canonical_sha256(left) == canonical_sha256(right)


def test_repository_configuration_loads_and_hashes_deterministically() -> None:
    bundle = get_config_bundle()
    assert {"app", "models", "pricing", "question_optimizer", "ranking", "slots"} <= set(bundle)
    assert len(config_bundle_hash()) == 64
    assert (REPOSITORY_ROOT / "config" / "app.yaml").is_file()
