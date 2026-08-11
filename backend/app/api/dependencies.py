from __future__ import annotations

from typing import cast

from fastapi import Header, Request

from backend.app.api.errors import ApiProblem
from backend.app.application.session_service import SnapshotSessionService
from backend.app.infrastructure.rate_limiter import FixedWindowRateLimiter, RateLimitKind


def get_session_service(request: Request) -> SnapshotSessionService:
    return cast(SnapshotSessionService, request.app.state.session_service)


def enforce_rate_limit(request: Request, kind: RateLimitKind) -> None:
    limiter = cast(FixedWindowRateLimiter, request.app.state.rate_limiter)
    client_ip = request.client.host if request.client is not None else "unknown"
    result = limiter.consume(client_ip, kind)
    if not result.allowed:
        raise ApiProblem(
            429,
            "RATE_LIMIT_EXCEEDED",
            "Rate limit exceeded",
            f"The {kind} hourly limit has been reached.",
            retryable=True,
        )


async def require_session_token(
    request: Request,
    session_id: str,
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
) -> str:
    if not x_session_token:
        raise ApiProblem(
            401,
            "SESSION_TOKEN_INVALID",
            "Invalid session token",
            "Session token is required.",
        )
    service = get_session_service(request)
    if not await service.authenticate(session_id, x_session_token):
        raise ApiProblem(
            401,
            "SESSION_TOKEN_INVALID",
            "Invalid session token",
            "Session token is invalid.",
        )
    return x_session_token
