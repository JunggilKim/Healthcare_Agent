from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import orjson
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from backend.app.domain.generation import ModelUsage, StructuredGenerationRecord
from backend.app.infrastructure.cache import ModelResultCache, model_cache_key
from backend.app.infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError
from backend.app.infrastructure.firestore_usage_guard import FirestoreUsageGuard
from backend.app.infrastructure.usage_guard import (
    CostGuardExceeded,
    InMemoryUsageGuard,
    PricingEstimator,
)

logger = logging.getLogger("trial_opt.model")

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class StructuredGenerationUnavailable(RuntimeError):
    pass


class StructuredGenerator:
    def __init__(
        self,
        *,
        client: genai.Client,
        cache: ModelResultCache,
        pricing: PricingEstimator,
        usage_guard: InMemoryUsageGuard | FirestoreUsageGuard | None = None,
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
                name=model_id,
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
        thinking_level: str | None,
        max_output_tokens: int,
        max_attempts: int,
        thinking_budget: int | None = None,
        attempt_timeout_seconds: float | None = None,
        session_id: str = "unscoped",
    ) -> tuple[OutputModel, StructuredGenerationRecord]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if attempt_timeout_seconds is not None and attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be positive")
        thinking_config, thinking_cache_config = self._thinking_config(
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
        )
        generation_config = {
            **thinking_cache_config,
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
        try:
            cached = await self.cache.get(cache_key)
        except Exception as error:
            logger.warning(
                "model cache read failed; continuing without cache "
                "(model_id=%s task_name=%s error=%s)",
                model_id,
                task_name,
                type(error).__name__,
            )
            cached = None
        if cached is not None and cached.cache_key == cache_key:
            try:
                parsed = output_model.model_validate(cached.parsed_json)
            except ValidationError:
                logger.warning(
                    "model cache payload validation failed; continuing without cache "
                    "(model_id=%s task_name=%s)",
                    model_id,
                    task_name,
                )
            else:
                cache_record = cached.model_copy(
                    update={"usage": cached.usage.model_copy(update={"cache_hit": True})}
                )
                logger.info(
                    self._usage_log(
                        cache_record,
                        session_id=session_id,
                        latency_ms=0,
                        retry_count=0,
                    )
                )
                return parsed, cache_record

        circuit = self.circuit(model_id)
        circuit.before_call()
        last_error: Exception | None = None
        retry_prompt = prompt
        for attempt in range(max_attempts):
            reservation = None
            billed_cost = None
            call_started = time.monotonic()
            try:
                if self.usage_guard is not None:
                    estimated_input_tokens = max(1, len(retry_prompt.encode()) // 4)
                    reserved_cost = self.pricing.reserved_generation_cost(
                        model_id,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                    )
                    reservation = await self.usage_guard.reserve_async(
                        session_id=session_id, amount_usd=reserved_cost
                    )
                request = self.client.aio.models.generate_content(
                    model=model_id,
                    contents=retry_prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_output_tokens,
                        response_mime_type="application/json",
                        response_json_schema=output_model.model_json_schema(),
                        thinking_config=thinking_config,
                    ),
                )
                if attempt_timeout_seconds is None:
                    response = await request
                else:
                    async with asyncio.timeout(attempt_timeout_seconds):
                        response = await request
                metadata = response.usage_metadata
                prompt_tokens = int(metadata.prompt_token_count or 0) if metadata else 0
                output_tokens = int(metadata.candidates_token_count or 0) if metadata else 0
                reasoning_tokens = int(metadata.thoughts_token_count or 0) if metadata else 0
                billed_cost = self.pricing.generation_cost(
                    model_id,
                    input_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
                parsed = output_model.model_validate_json(response.text or "")
                if reservation is not None:
                    assert self.usage_guard is not None
                    await self.usage_guard.reconcile_async(
                        reservation.reservation_id, actual_usd=billed_cost
                    )
                    reservation = None
                usage = ModelUsage(
                    model_id=model_id,
                    task_name=task_name,
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=int(metadata.total_token_count or 0) if metadata else 0,
                    estimated_cost_usd=float(billed_cost),
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
                try:
                    await self.cache.put(record)
                except Exception as error:
                    # The model response is already validated and any reservation is reconciled.
                    # A cache outage must not trigger another paid model dispatch.
                    logger.warning(
                        "model cache write failed; returning validated response "
                        "(model_id=%s task_name=%s error=%s)",
                        model_id,
                        task_name,
                        type(error).__name__,
                    )
                logger.info(
                    self._usage_log(
                        record,
                        session_id=session_id,
                        latency_ms=(time.monotonic() - call_started) * 1000,
                        retry_count=attempt,
                    )
                )
                circuit.record_success()
                return parsed, record
            except CostGuardExceeded as error:
                raise StructuredGenerationUnavailable(str(error)) from error
            except Exception as error:
                if reservation is not None and self.usage_guard is not None:
                    if billed_cost is None:
                        await self.usage_guard.release_async(reservation.reservation_id)
                    else:
                        await self.usage_guard.reconcile_async(
                            reservation.reservation_id, actual_usd=billed_cost
                        )
                last_error = error
                logger.warning(
                    self._attempt_failure_log(
                        model_id=model_id,
                        task_name=task_name,
                        session_id=session_id,
                        latency_ms=(time.monotonic() - call_started) * 1000,
                        retry_count=attempt,
                        error=error,
                    )
                )
                if attempt < max_attempts - 1:
                    issue = (
                        self._validation_issue_text(error)
                        if isinstance(error, ValidationError)
                        else type(error).__name__
                    )
                    retry_prompt = (
                        f"{prompt}\n\nVALIDATION_RETRY_ISSUE\n{issue[:1000]}\n"
                        "Return one complete compact JSON object immediately. "
                        "Do not add prose or markdown."
                    )
                    delay = (1, 2, 4)[min(attempt, 2)] + float(self._jitter(0.0, 0.5))
                    await self._sleep(delay)
        circuit.record_failure()
        cause = type(last_error).__name__ if last_error is not None else "UnknownError"
        issue_summary = self._error_summary(last_error)
        raise StructuredGenerationUnavailable(
            f"structured generation failed for {task_name} after {max_attempts} attempts "
            f"(last_error={cause}{issue_summary})"
        ) from last_error

    @staticmethod
    def _error_summary(error: Exception | None) -> str:
        if not isinstance(error, ValidationError):
            return ""
        issues = []
        for issue in error.errors(include_input=False, include_url=False)[:8]:
            location = ".".join(str(item) for item in issue["loc"]) or "$"
            issues.append(f"{location}:{issue['type']}")
        return f"; issues={','.join(issues)}"

    @staticmethod
    def _validation_issue_text(error: ValidationError) -> str:
        issues = []
        for issue in error.errors(include_input=False, include_url=False)[:8]:
            location = ".".join(str(item) for item in issue["loc"]) or "$"
            issues.append(f"{location}:{issue['type']}")
        return ",".join(issues)

    @staticmethod
    def _thinking_config(
        *, thinking_level: str | None, thinking_budget: int | None
    ) -> tuple[types.ThinkingConfig, dict[str, str | int]]:
        if (thinking_level is None) == (thinking_budget is None):
            raise ValueError("exactly one of thinking_level or thinking_budget is required")
        if thinking_budget is not None:
            if thinking_budget <= 0:
                raise ValueError("thinking_budget must be positive")
            return (
                types.ThinkingConfig(thinking_budget=thinking_budget),
                {"thinking_budget": thinking_budget},
            )
        assert thinking_level is not None
        return (
            types.ThinkingConfig(thinking_level=types.ThinkingLevel(thinking_level)),
            {"thinking_level": thinking_level},
        )

    @staticmethod
    def _usage_log(
        record: StructuredGenerationRecord,
        *,
        session_id: str,
        latency_ms: float,
        retry_count: int,
    ) -> str:
        usage = record.usage
        return orjson.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "severity": "INFO",
                "request_id": None,
                "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest(),
                "event_type": "model_call",
                "stage": record.task_name,
                "mode": None,
                "model_id": record.model_id,
                "task_name": record.task_name,
                "cache_hit": usage.cache_hit,
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.output_tokens,
                "estimated_cost_usd": usage.estimated_cost_usd,
                "latency_ms": round(latency_ms, 3),
                "retry_count": retry_count,
                "degradation_code": None,
                "error_code": None,
                "git_sha": os.getenv("APP_VERSION", "dev"),
            }
        ).decode()

    @staticmethod
    def _attempt_failure_log(
        *,
        model_id: str,
        task_name: str,
        session_id: str,
        latency_ms: float,
        retry_count: int,
        error: Exception,
    ) -> str:
        return orjson.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "severity": "WARNING",
                "request_id": None,
                "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest(),
                "event_type": "model_call_attempt_failed",
                "stage": task_name,
                "mode": None,
                "model_id": model_id,
                "task_name": task_name,
                "cache_hit": False,
                "input_tokens": None,
                "output_tokens": None,
                "estimated_cost_usd": None,
                "latency_ms": round(latency_ms, 3),
                "retry_count": retry_count,
                "degradation_code": None,
                "error_code": type(error).__name__,
                "git_sha": os.getenv("APP_VERSION", "dev"),
            }
        ).decode()

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
        primary_thinking_level: str | None,
        fallback_thinking_level: str | None,
        primary_max_output_tokens: int,
        fallback_max_output_tokens: int,
        primary_thinking_budget: int | None = None,
        fallback_thinking_budget: int | None = None,
        primary_max_attempts: int = 3,
        fallback_max_attempts: int = 1,
        primary_attempt_timeout_seconds: float | None = None,
        fallback_attempt_timeout_seconds: float | None = None,
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
                thinking_budget=primary_thinking_budget,
                max_output_tokens=primary_max_output_tokens,
                max_attempts=primary_max_attempts,
                attempt_timeout_seconds=primary_attempt_timeout_seconds,
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
                thinking_budget=fallback_thinking_budget,
                max_output_tokens=fallback_max_output_tokens,
                max_attempts=fallback_max_attempts,
                attempt_timeout_seconds=fallback_attempt_timeout_seconds,
                session_id=session_id,
            )
            return parsed, record.model_copy(update={"used_fallback": True})
