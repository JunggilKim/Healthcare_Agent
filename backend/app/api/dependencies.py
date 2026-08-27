from __future__ import annotations

import ipaddress
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


def rate_limit_subject(request: Request) -> str:
    """Return the client address added by the trusted Google frontend.

    Google load balancers append ``client-ip, load-balancer-ip`` to any
    caller-supplied X-Forwarded-For values. Reading the second-to-last hop
    avoids both the shared Cloud Run proxy address and spoofable leading hops.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [item.strip() for item in forwarded.split(",")]
        if len(hops) >= 2:
            candidate = hops[-2]
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return request.client.host if request.client is not None else "unknown"


async def enforce_rate_limit(request: Request, kind: RateLimitKind) -> None:
    limiter = cast(
        FixedWindowRateLimiter | FirestoreFixedWindowRateLimiter,
        request.app.state.rate_limiter,
    )
    client_ip = rate_limit_subject(request)
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
