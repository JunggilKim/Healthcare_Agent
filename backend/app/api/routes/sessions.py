from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import orjson
from fastapi import APIRouter, Depends, Request, status
from pydantic import Field, model_validator
from starlette.responses import StreamingResponse

from backend.app.api.dependencies import (
    enforce_rate_limit,
    get_session_service,
    require_session_token,
)
from backend.app.api.errors import ApiProblem
from backend.app.application.session_service import SnapshotSessionService
from backend.app.domain.base import StrictModel

router = APIRouter(tags=["sessions"])


class CreateSessionRequest(StrictModel):
    mode: str
    patient_text: str | None = Field(default=None, max_length=12000)
    seed_case_id: str | None = None
    evaluation_date: str
    language: str = "auto"
    confirm_synthetic_public: bool = False
    identifier_warning_acknowledged: bool = False

    @model_validator(mode="after")
    def exactly_one_input(self) -> CreateSessionRequest:
        if (self.patient_text is None) == (self.seed_case_id is None):
            raise ValueError("exactly one of patient_text or seed_case_id is required")
        if self.patient_text is not None and not self.patient_text.strip():
            raise ValueError("patient_text cannot be blank")
        return self


class SubmitAnswerRequest(StrictModel):
    question_id: str
    answer_text: str | None = Field(default=None, max_length=4000)
    structured_value: dict[str, object] | None = None
    unknown: bool = False
    declined: bool = False

    @model_validator(mode="after")
    def one_answer_form(self) -> SubmitAnswerRequest:
        supplied = sum(
            [
                bool(self.answer_text),
                self.structured_value is not None,
                self.unknown,
                self.declined,
            ]
        )
        if supplied != 1:
            raise ValueError("exactly one answer form is required")
        return self


def _sse(event_name: str, payload: dict[str, object]) -> bytes:
    return b"event: " + event_name.encode() + b"\ndata: " + orjson.dumps(payload) + b"\n\n"


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    service: SnapshotSessionService = Depends(get_session_service),
) -> dict[str, object]:
    enforce_rate_limit(request, "snapshot_session" if body.mode == "snapshot" else "live_session")
    if body.patient_text is not None:
        if not body.confirm_synthetic_public:
            raise ApiProblem(
                422,
                "INVALID_INPUT",
                "Synthetic/public confirmation required",
                "Arbitrary text requires explicit public or synthetic confirmation.",
            )
        raise ApiProblem(
            422,
            "SNAPSHOT_BRANCH_UNAVAILABLE",
            "Phase-1 snapshot scope",
            "The frozen Phase-1 milestone supports organizer seed S004 only.",
        )
    try:
        evaluation_date = date.fromisoformat(body.evaluation_date)
        return await service.create_session(
            mode=body.mode,
            seed_case_id=body.seed_case_id or "",
            evaluation_date=evaluation_date,
            language=body.language,
        )
    except (ValueError, TypeError) as error:
        raise ApiProblem(422, "INVALID_INPUT", "Invalid session input", str(error)) from error


@router.post("/sessions/{session_id}/analysis")
async def start_analysis(
    session_id: str,
    request: Request,
    _: str = Depends(require_session_token),
    service: SnapshotSessionService = Depends(get_session_service),
) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        try:
            async for event_name, payload in service.analyze(session_id):
                yield _sse(event_name, payload)
        except KeyError:
            yield _sse("error", {"code": "SESSION_NOT_FOUND"})

    del request
    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/sessions/{session_id}")
async def read_session(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SnapshotSessionService = Depends(get_session_service),
) -> dict[str, object]:
    payload = await service.read_session(session_id)
    if payload is None:
        raise ApiProblem(
            404,
            "SESSION_NOT_FOUND",
            "Session not found",
            "The session does not exist.",
        )
    return payload


@router.post("/sessions/{session_id}/answers")
async def submit_answer(
    session_id: str,
    body: SubmitAnswerRequest,
    request: Request,
    _: str = Depends(require_session_token),
    service: SnapshotSessionService = Depends(get_session_service),
) -> StreamingResponse:
    enforce_rate_limit(request, "answer_submission")

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for event_name, payload in service.submit_answer(
                session_id,
                question_id=body.question_id,
                answer_text=body.answer_text,
                unknown=body.unknown,
                declined=body.declined,
            ):
                yield _sse(event_name, payload)
        except ValueError as error:
            yield _sse("error", {"code": str(error)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/sessions/{session_id}/trials/{nct_id}/proof")
async def read_proof(
    session_id: str,
    nct_id: str,
    _: str = Depends(require_session_token),
    service: SnapshotSessionService = Depends(get_session_service),
) -> dict[str, object]:
    payload = await service.read_proof(session_id, nct_id)
    if payload is None:
        raise ApiProblem(404, "SESSION_NOT_FOUND", "Proof not found", "The proof is not available.")
    return payload
