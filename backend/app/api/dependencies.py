from __future__ import annotations

from fastapi import Header, Request

from backend.app.api.errors import ApiProblem
from backend.app.application.session_service import SnapshotSessionService


def get_session_service(request: Request) -> SnapshotSessionService:
    return request.app.state.session_service


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
