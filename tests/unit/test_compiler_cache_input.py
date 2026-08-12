from __future__ import annotations

from datetime import UTC, datetime

from backend.app.application.compilation_service import compiler_generation_input
from backend.app.application.vertical_slice import load_vertical_slice


def test_compiler_cache_input_excludes_registry_transport_metadata() -> None:
    original = load_vertical_slice().raw_trial
    refreshed = original.model_copy(
        update={
            "retrieved_at": datetime(2026, 8, 13, tzinfo=UTC),
            "raw_gcs_uri": "gs://different-runtime-location/raw.json",
            "api_version": "2.0.6",
        }
    )

    assert original != refreshed
    assert compiler_generation_input(original) == compiler_generation_input(refreshed)


def test_compiler_cache_input_changes_with_prompt_visible_semantics() -> None:
    original = load_vertical_slice().raw_trial
    changed = original.model_copy(update={"minimum_age": "21 Years"})

    assert compiler_generation_input(original) != compiler_generation_input(changed)
