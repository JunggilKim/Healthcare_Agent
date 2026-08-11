from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class SessionRepository(Protocol):
    async def health(self) -> bool: ...


class EventRepository(Protocol):
    async def stream(self, session_id: str) -> AsyncIterator[object]: ...


class ArtifactRepository(Protocol):
    async def health(self) -> bool: ...


class CompiledTrialRepository(Protocol):
    async def health(self) -> bool: ...


class LlmCacheRepository(Protocol):
    async def health(self) -> bool: ...


class UsageRepository(Protocol):
    async def health(self) -> bool: ...
