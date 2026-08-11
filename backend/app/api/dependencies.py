from __future__ import annotations

from typing import cast

from fastapi import Header, Request

from backend.app.api.errors import ApiProblem
from backend.app.application.session_router import SessionService
from backend.app.infrastructure.rate_limiter import (
    FirestoreFixedWindowRateLimiter,
    FixedWindowRateLimiter,
    RateLimitKind,
)


def get_session_service(request: Request) -> SessionService:
    return cast(SessionService, request.app.state.session_service)


async def enforce_rate_limit(request: Request, kind: RateLimitKind) -> None:
    limiter = cast(
        FixedWindowRateLimiter | FirestoreFixedWindowRateLimiter,
        request.app.state.rate_limiter,
    )
    client_ip = request.client.host if request.client is not None else "unknown"
    result = await limiter.consume_async(client_ip, kind)
    if not result.allowed:
        raise ApiProblem(
            429,
            "RATE_LIMITED",
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
