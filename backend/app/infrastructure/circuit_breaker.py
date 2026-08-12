from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str = "dependency"
    failure_threshold: int = 5
    recovery_seconds: float = 60.0
    failure_window_seconds: float | None = 60.0
    clock: Callable[[], float] = time.monotonic
    consecutive_failures: int = 0
    opened_at: float | None = None
    failure_times: list[float] | None = None

    def __post_init__(self) -> None:
        if self.failure_times is None:
            self.failure_times = []

    @property
    def state(self) -> CircuitState:
        if self.opened_at is None:
            return CircuitState.CLOSED
        if self.clock() - self.opened_at >= self.recovery_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def before_call(self) -> None:
        if self.state is CircuitState.OPEN:
            raise CircuitOpenError(f"{self.name} circuit is open")

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        assert self.failure_times is not None
        self.failure_times.clear()

    def record_failure(self) -> None:
        now = self.clock()
        assert self.failure_times is not None
        if self.failure_window_seconds is not None:
            cutoff = now - self.failure_window_seconds
            self.failure_times[:] = [value for value in self.failure_times if value >= cutoff]
        self.failure_times.append(now)
        self.consecutive_failures = len(self.failure_times)
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = now
