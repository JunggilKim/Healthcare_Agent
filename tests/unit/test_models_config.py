from __future__ import annotations

from backend.app.settings import get_config_bundle


def test_models_are_frozen_and_forbidden_patterns_absent() -> None:
    models = get_config_bundle()["models"]
    configured = [entry["id"] for entry in models["models"].values()]
    assert configured == ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-embedding-001"]
    assert models["consumption"]["priority_paygo_allowed"] is False
    for model_id in configured:
        assert all(pattern not in model_id for pattern in models["forbidden_patterns"])
