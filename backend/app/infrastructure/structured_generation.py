from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from backend.app.domain.generation import ModelUsage, StructuredGenerationRecord
from backend.app.infrastructure.cache import LocalModelResultCache, model_cache_key
from backend.app.infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError
from backend.app.infrastructure.usage_guard import (
    CostGuardExceeded,
    InMemoryUsageGuard,
    PricingEstimator,
)

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class StructuredGenerationUnavailable(RuntimeError):
    pass


class StructuredGenerator:
    def __init__(
        self,
        *,
        client: genai.Client,
        cache: LocalModelResultCache,
        pricing: PricingEstimator,
        usage_guard: InMemoryUsageGuard | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.client = client
        self.cache = cache
        self.pricing = pricing
        self.usage_guard = usage_guard
        self._sleep = sleep
        self._jitter = jitter
        self._circuits: dict[str, CircuitBreaker] = {}

    def circuit(self, model_id: str) -> CircuitBreaker:
        return self._circuits.setdefault(
            model_id,
            CircuitBreaker(
                failure_threshold=5,
                failure_window_seconds=120,
                recovery_seconds=90,
            ),
        )

    async def generate(
        self,
        *,
        model_id: str,
        task_name: str,
        prompt: str,
        prompt_version: str,
        output_schema_version: str,
        slot_catalog_version: str,
        normalized_input: object,
        output_model: type[OutputModel],
        thinking_level: str,
        max_output_tokens: int,
        max_attempts: int,
        session_id: str = "unscoped",
    ) -> tuple[OutputModel, StructuredGenerationRecord]:
        generation_config = {
            "thinking_level": thinking_level,
            "max_output_tokens": max_output_tokens,
        }
        cache_key = model_cache_key(
            model_id=model_id,
            task_name=task_name,
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
            slot_catalog_version=slot_catalog_version,
            normalized_input=normalized_input,
            generation_config=generation_config,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            parsed = output_model.model_validate(cached.parsed_json)
            return parsed, cached.model_copy(
                update={"usage": cached.usage.model_copy(update={"cache_hit": True})}
            )

        circuit = self.circuit(model_id)
        circuit.before_call()
        last_error: Exception | None = None
        retry_prompt = prompt
        for attempt in range(max_attempts):
            reservation = None
            try:
                if self.usage_guard is not None:
                    estimated_input_tokens = max(1, len(retry_prompt.encode()) // 4)
                    reserved_cost = self.pricing.reserved_generation_cost(
                        model_id,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                    )
                    reservation = self.usage_guard.reserve(
                        session_id=session_id, amount_usd=reserved_cost
                    )
                response = await self.client.aio.models.generate_content(
                    model=model_id,
                    contents=retry_prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_output_tokens,
                        response_mime_type="application/json",
                        response_json_schema=output_model.model_json_schema(),
                        thinking_config=types.ThinkingConfig(
                            thinking_level=types.ThinkingLevel(thinking_level)
                        ),
                    ),
                )
                parsed = output_model.model_validate_json(response.text or "")
                metadata = response.usage_metadata
                prompt_tokens = int(metadata.prompt_token_count or 0) if metadata else 0
                output_tokens = int(metadata.candidates_token_count or 0) if metadata else 0
                reasoning_tokens = int(metadata.thoughts_token_count or 0) if metadata else 0
                cost = self.pricing.generation_cost(
                    model_id,
                    input_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
                if reservation is not None:
                    assert self.usage_guard is not None
                    self.usage_guard.reconcile(reservation.reservation_id, actual_usd=cost)
                usage = ModelUsage(
                    model_id=model_id,
                    task_name=task_name,
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=int(metadata.total_token_count or 0) if metadata else 0,
                    estimated_cost_usd=float(cost),
                    cache_hit=False,
                    created_at=datetime.now(UTC),
                )
                record = StructuredGenerationRecord(
                    cache_key=cache_key,
                    model_id=model_id,
                    task_name=task_name,
                    prompt_version=prompt_version,
                    output_schema_version=output_schema_version,
                    parsed_json=parsed.model_dump(mode="json"),
                    usage=usage,
                )
                self.cache.put(record)
                circuit.record_success()
                return parsed, record
            except CostGuardExceeded as error:
                raise StructuredGenerationUnavailable(str(error)) from error
            except Exception as error:
                last_error = error
                if attempt < max_attempts - 1:
                    issue = (
                        str(error) if isinstance(error, ValidationError) else type(error).__name__
                    )
                    retry_prompt = f"{prompt}\n\nVALIDATION_RETRY_ISSUE\n{issue[:1000]}"
                    delay = (1, 2, 4)[min(attempt, 2)] + float(self._jitter(0.0, 0.5))
                    await self._sleep(delay)
        circuit.record_failure()
        raise StructuredGenerationUnavailable(
            f"structured generation failed for {task_name} after {max_attempts} attempts"
        ) from last_error

    async def generate_primary_with_lite_fallback(
        self,
        *,
        primary_model_id: str,
        lite_model_id: str,
        task_name: str,
        prompt: str,
        prompt_version: str,
        output_schema_version: str,
        slot_catalog_version: str,
        normalized_input: object,
        output_model: type[OutputModel],
        primary_thinking_level: str,
        fallback_thinking_level: str,
        primary_max_output_tokens: int,
        fallback_max_output_tokens: int,
        session_id: str = "unscoped",
    ) -> tuple[OutputModel, StructuredGenerationRecord]:
        try:
            return await self.generate(
                model_id=primary_model_id,
                task_name=task_name,
                prompt=prompt,
                prompt_version=prompt_version,
                output_schema_version=output_schema_version,
                slot_catalog_version=slot_catalog_version,
                normalized_input=normalized_input,
                output_model=output_model,
                thinking_level=primary_thinking_level,
                max_output_tokens=primary_max_output_tokens,
                max_attempts=3,
                session_id=session_id,
            )
        except (StructuredGenerationUnavailable, CircuitOpenError):
            parsed, record = await self.generate(
                model_id=lite_model_id,
                task_name=task_name,
                prompt=prompt,
                prompt_version=prompt_version,
                output_schema_version=output_schema_version,
                slot_catalog_version=slot_catalog_version,
                normalized_input=normalized_input,
                output_model=output_model,
                thinking_level=fallback_thinking_level,
                max_output_tokens=fallback_max_output_tokens,
                max_attempts=1,
                session_id=session_id,
            )
            return parsed, record.model_copy(update={"used_fallback": True})
