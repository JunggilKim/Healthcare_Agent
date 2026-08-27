from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import orjson
from fastapi import Request, Response

from backend.app.settings import get_settings

logger = logging.getLogger("trial_opt.request")


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-Id") or str(uuid4())
    request.state.request_id = request_id
    started = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path.startswith("/api/v1/sessions"):
            response.headers["Cache-Control"] = "no-store"
        elif (
            request.url.path == "/"
            or request.url.path == "/about"
            or request.url.path.startswith("/session/")
        ):
            # The HTML shell contains content-hashed asset names. It must never be
            # reused across deployments, otherwise a browser can keep booting an
            # old bundle even though the new revision is already serving traffic.
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        return response
    finally:
        route = request.scope.get("route")
        route_template = getattr(route, "path", "unmatched")
        session_id = request.path_params.get("session_id", "")
        settings = get_settings()
        logger.info(
            orjson.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "severity": "INFO" if status_code < 500 else "ERROR",
                    "request_id": request_id,
                    "session_id_hash": (
                        hashlib.sha256(session_id.encode()).hexdigest() if session_id else None
                    ),
                    "event_type": "http_request",
                    "stage": route_template,
                    "mode": None,
                    "model_id": None,
                    "task_name": None,
                    "cache_hit": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "estimated_cost_usd": None,
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "retry_count": 0,
                    "degradation_code": None,
                    "error_code": None if status_code < 400 else f"HTTP_{status_code}",
                    "git_sha": settings.app_version,
                }
            ).decode()
        )
