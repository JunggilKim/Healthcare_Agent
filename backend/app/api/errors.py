from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class ApiProblem(Exception):
    status: int
    code: str
    title: str
    detail: str
    retryable: bool = False
    headers: dict[str, str] | None = None


async def problem_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ApiProblem):
        raise error
    problem = error
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
        headers=problem.headers,
    )


async def validation_problem_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    issues = error.errors()
    too_large = any(
        issue.get("type") == "string_too_long"
        and "patient_text" in [str(part) for part in issue.get("loc", ())]
        for issue in issues
    )
    code = "INPUT_TOO_LARGE" if too_large else "INVALID_INPUT"
    fields = sorted(
        {".".join(str(part) for part in issue.get("loc", ()) if part != "body") for issue in issues}
    )
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "type": f"https://trial-opt.local/problems/{code.lower().replace('_', '-')}",
            "title": "Input validation failed",
            "status": 422,
            "code": code,
            "detail": "Invalid fields: " + ", ".join(fields),
            "request_id": request_id,
            "retryable": False,
        },
    )
