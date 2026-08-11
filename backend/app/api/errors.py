from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class ApiProblem(Exception):
    status: int
    code: str
    title: str
    detail: str
    retryable: bool = False


async def problem_handler(request: Request, problem: ApiProblem) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=problem.status,
        media_type="application/problem+json",
        content={
            "type": f"https://trial-opt.local/problems/{problem.code.lower().replace('_', '-')}",
            "title": problem.title,
            "status": problem.status,
            "code": problem.code,
            "detail": problem.detail,
            "request_id": request_id,
            "retryable": problem.retryable,
        },
    )
