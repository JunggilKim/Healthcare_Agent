from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.domain.base import StrictModel
from backend.app.infrastructure.cache import LocalModelResultCache, model_cache_key
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)
from backend.app.infrastructure.usage_guard import InMemoryUsageGuard, default_pricing_estimator


class _Output(StrictModel):
    value: str


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.thinking_budgets: list[int | None] = []
        self.thinking_levels: list[object | None] = []

    async def generate_content(self, *, model, contents, config):
        del contents
        self.calls.append(model)
        self.thinking_budgets.append(config.thinking_config.thinking_budget)
        self.thinking_levels.append(config.thinking_config.thinking_level)
        text = "not json" if len(self.calls) == 1 else '{"value":"ok"}'
        usage = SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=10,
            thoughts_token_count=5,
            total_token_count=115,
        )
        return SimpleNamespace(text=text, usage_metadata=usage)


class _FakeClient:
    def __init__(self) -> None:
        self.aio = SimpleNamespace(models=_FakeModels())


class _FallbackModels:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_content(self, *, model, contents, config):
        del contents, config
        self.calls.append(model)
        text = '{"value":"fallback"}' if model == "gemini-3.5-flash-lite" else "invalid"
        return SimpleNamespace(text=text, usage_metadata=None)


class _FailureModels:
    def __init__(self, failure: str) -> None:
        self.failure = failure
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        del model, contents, config
        self.calls += 1
        if self.failure == "timeout":
            raise TimeoutError("recorded Gemini timeout")
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


async def _no_sleep(_delay: float) -> None:
    await asyncio.sleep(0)


def test_cache_key_binds_all_normative_parts() -> None:
    first = model_cache_key(
        model_id="m",
        task_name="t",
        prompt_version="1",
        output_schema_version="1",
        slot_catalog_version="1",
        normalized_input={"b": 2, "a": 1},
        generation_config={"thinking": "HIGH"},
    )
    same = model_cache_key(
        model_id="m",
        task_name="t",
        prompt_version="1",
        output_schema_version="1",
        slot_catalog_version="1",
        normalized_input={"a": 1, "b": 2},
        generation_config={"thinking": "HIGH"},
    )
    changed = model_cache_key(
        model_id="m",
        task_name="t",
        prompt_version="2",
        output_schema_version="1",
        slot_catalog_version="1",
        normalized_input={"a": 1, "b": 2},
        generation_config={"thinking": "HIGH"},
    )
    assert first == same
    assert first != changed


async def test_schema_failure_retries_then_exact_cache_prevents_second_dispatch(
    tmp_path: Path,
) -> None:
    client = _FakeClient()
    usage_guard = InMemoryUsageGuard()
    generator = StructuredGenerator(
        client=client,
        cache=LocalModelResultCache(tmp_path),
        pricing=default_pricing_estimator(),
        usage_guard=usage_guard,
        sleep=_no_sleep,
        jitter=lambda _low, _high: 0,
    )
    arguments = {
        "model_id": "gemini-3.6-flash",
        "task_name": "test",
        "prompt": "return json",
        "prompt_version": "1.0.0",
        "output_schema_version": "test-v1",
        "slot_catalog_version": "slot-catalog-v1",
        "normalized_input": {"x": 1},
        "output_model": _Output,
        "thinking_level": "MEDIUM",
        "max_output_tokens": 100,
        "max_attempts": 3,
    }
    first, first_record = await generator.generate(**arguments)
    second, second_record = await generator.generate(**arguments)
    assert first.value == second.value == "ok"
    assert client.aio.models.calls == ["gemini-3.6-flash", "gemini-3.6-flash"]
    assert first_record.usage.cache_hit is False
    assert second_record.usage.cache_hit is True
    assert first_record.usage.estimated_cost_usd > 0
    usage = usage_guard.snapshot("unscoped")
    assert usage.session_reserved_usd == 0
    assert usage.session_reconciled_usd == 2 * default_pricing_estimator().generation_cost(
        "gemini-3.6-flash",
        input_tokens=100,
        output_tokens=10,
        reasoning_tokens=5,
    )


async def test_primary_schema_exhaustion_uses_single_lite_fallback(tmp_path: Path) -> None:
    client = _FakeClient()
    client.aio.models = _FallbackModels()
    generator = StructuredGenerator(
        client=client,
        cache=LocalModelResultCache(tmp_path),
        pricing=default_pricing_estimator(),
        sleep=_no_sleep,
        jitter=lambda _low, _high: 0,
    )
    output, record = await generator.generate_primary_with_lite_fallback(
        primary_model_id="gemini-3.6-flash",
        lite_model_id="gemini-3.5-flash-lite",
        task_name="compiler",
        prompt="return json",
        prompt_version="1.0.0",
        output_schema_version="test-v1",
        slot_catalog_version="slot-catalog-v1",
        normalized_input={"x": 1},
        output_model=_Output,
        primary_thinking_level="HIGH",
        fallback_thinking_level="HIGH",
        primary_max_output_tokens=100,
        fallback_max_output_tokens=100,
    )
    assert output.value == "fallback"
    assert record.used_fallback is True
    assert client.aio.models.calls == [
        "gemini-3.6-flash",
        "gemini-3.6-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
    ]


async def test_explicit_thinking_budget_is_sent_and_cache_bound(tmp_path: Path) -> None:
    client = _FakeClient()
    generator = StructuredGenerator(
        client=client,
        cache=LocalModelResultCache(tmp_path),
        pricing=default_pricing_estimator(),
        sleep=_no_sleep,
        jitter=lambda _low, _high: 0,
    )
    output, _record = await generator.generate(
        model_id="gemini-3.6-flash",
        task_name="protocol_compiler",
        prompt="return compact json",
        prompt_version="1.0.4",
        output_schema_version="test-v1",
        slot_catalog_version="slot-catalog-v1",
        normalized_input={"x": 1},
        output_model=_Output,
        thinking_level=None,
        thinking_budget=1024,
        max_output_tokens=4000,
        max_attempts=2,
    )
    assert output.value == "ok"
    assert client.aio.models.thinking_budgets == [1024, 1024]
    assert client.aio.models.thinking_levels == [None, None]


@pytest.mark.parametrize(
    ("thinking_level", "thinking_budget"),
    [(None, None), ("HIGH", 1024), (None, 0)],
)
async def test_thinking_configuration_requires_one_supported_mode(
    tmp_path: Path,
    thinking_level: str | None,
    thinking_budget: int | None,
) -> None:
    generator = StructuredGenerator(
        client=_FakeClient(),
        cache=LocalModelResultCache(tmp_path),
        pricing=default_pricing_estimator(),
    )
    with pytest.raises(ValueError):
        await generator.generate(
            model_id="gemini-3.6-flash",
            task_name="test",
            prompt="return json",
            prompt_version="1",
            output_schema_version="test-v1",
            slot_catalog_version="slot-catalog-v1",
            normalized_input={},
            output_model=_Output,
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
            max_output_tokens=100,
            max_attempts=1,
        )


@pytest.mark.parametrize("failure", ["timeout", "429"])
async def test_gemini_timeout_and_429_exhaust_bounded_retries(tmp_path: Path, failure: str) -> None:
    client = _FakeClient()
    models = _FailureModels(failure)
    client.aio.models = models
    generator = StructuredGenerator(
        client=client,
        cache=LocalModelResultCache(tmp_path),
        pricing=default_pricing_estimator(),
        sleep=_no_sleep,
        jitter=lambda _low, _high: 0,
    )
    with pytest.raises(StructuredGenerationUnavailable):
        await generator.generate(
            model_id="gemini-3.6-flash",
            task_name="fault-test",
            prompt="return json",
            prompt_version="1.0.0",
            output_schema_version="test-v1",
            slot_catalog_version="slot-catalog-v1",
            normalized_input={"x": 1},
            output_model=_Output,
            thinking_level="MEDIUM",
            max_output_tokens=100,
            max_attempts=3,
        )
    assert models.calls == 3


def test_pricing_estimator_uses_configured_prices() -> None:
    estimator = default_pricing_estimator()
    assert estimator.generation_cost(
        "gemini-3.6-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    ) == Decimal("9.00")
    assert (
        estimator.reserved_generation_cost(
            "gemini-3.5-flash-lite",
            estimated_input_tokens=4000,
            max_output_tokens=800,
        )
        > 0
    )
