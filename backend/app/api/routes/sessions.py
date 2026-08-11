from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import date
from html import escape
from typing import Literal

import orjson
from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import Field, model_validator
from starlette.responses import HTMLResponse, StreamingResponse

from backend.app.api.dependencies import (
    enforce_rate_limit,
    get_session_service,
    require_session_token,
)
from backend.app.api.errors import ApiProblem
from backend.app.application.session_router import SessionService
from backend.app.domain.base import StrictModel
from backend.app.infrastructure.resilient_gcp_store import PersistenceUnavailableError
from backend.app.security.pii_detector import detect_identifier_ranges

router = APIRouter(tags=["sessions"])


class CreateSessionRequest(StrictModel):
    mode: Literal["snapshot", "live"]
    patient_text: str | None = Field(default=None, max_length=12000)
    seed_case_id: str | None = None
    evaluation_date: str
    language: Literal["ko", "en", "auto"] = "auto"
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


def _sse_json(event_name: str, payload_json: str) -> bytes:
    return b"event: " + event_name.encode() + b"\ndata: " + payload_json.encode() + b"\n\n"


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    await enforce_rate_limit(
        request, "snapshot_session" if body.mode == "snapshot" else "live_session"
    )
    if body.patient_text is not None:
        if not body.confirm_synthetic_public:
            raise ApiProblem(
                422,
                "INVALID_INPUT",
                "Synthetic/public confirmation required",
                "Arbitrary text requires explicit public or synthetic confirmation.",
            )
        identifier_matches = detect_identifier_ranges(body.patient_text)
        if identifier_matches and not body.identifier_warning_acknowledged:
            raise ApiProblem(
                422,
                "PII_WARNING_REQUIRED",
                "Identifier warning acknowledgement required",
                "Potential identifier categories/ranges: "
                + ", ".join(
                    f"{item.category}[{item.start}:{item.end}]" for item in identifier_matches
                ),
            )
    try:
        evaluation_date = date.fromisoformat(body.evaluation_date)
        return await service.create_session(
            mode=body.mode,
            seed_case_id=body.seed_case_id or "",
            patient_text=body.patient_text,
            evaluation_date=evaluation_date,
            language=body.language,
        )
    except ValueError as error:
        code = str(error).split(":", maxsplit=1)[0]
        if code in {
            "SNAPSHOT_ARBITRARY_TEXT_UNAVAILABLE",
            "SNAPSHOT_CASE_UNAVAILABLE",
        }:
            raise ApiProblem(
                422,
                "SNAPSHOT_BRANCH_UNAVAILABLE",
                "Snapshot branch unavailable",
                str(error),
            ) from error
        if code == "LIVE_DEPENDENCIES_DISABLED":
            raise ApiProblem(
                503,
                code,
                "Live Mode dependencies disabled",
                str(error),
                retryable=False,
            ) from error
        raise ApiProblem(422, "INVALID_INPUT", "Invalid session input", str(error)) from error
    except TypeError as error:
        raise ApiProblem(422, "INVALID_INPUT", "Invalid session input", str(error)) from error


@router.post("/sessions/{session_id}/analysis")
async def start_analysis(
    session_id: str,
    request: Request,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> StreamingResponse:
    session = await service.read_session(session_id)
    if session is None:
        raise ApiProblem(
            404, "SESSION_NOT_FOUND", "Session not found", "The session does not exist."
        )
    lease_owner = secrets.token_urlsafe(18)
    if not await service.acquire_analysis_lease(session_id, lease_owner):
        raise ApiProblem(
            409,
            "SESSION_BUSY",
            "Session is busy",
            "Another analysis request holds the session orchestration lease.",
            retryable=True,
        )
    try:
        if session.get("mode") == "live" and session.get("state") == "CREATED":
            await enforce_rate_limit(request, "cold_compile")
    except Exception:
        await service.release_analysis_lease(session_id, lease_owner)
        raise

    async def stream() -> AsyncIterator[bytes]:
        iterator = service.analyze(session_id).__aiter__()
        pending: asyncio.Future[tuple[str, dict[str, object]]] | None = None
        sequence = 0
        try:
            pending = asyncio.ensure_future(iterator.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=10)
                if not done:
                    sequence += 1
                    renewed = await service.renew_analysis_lease(session_id, lease_owner)
                    yield _sse(
                        "heartbeat",
                        {
                            "sequence": sequence,
                            "state": str(session.get("state", "CREATED")),
                            "lease_renewed": renewed,
                        },
                    )
                    continue
                try:
                    event_name, payload = pending.result()
                except StopAsyncIteration:
                    break
                sequence += 1
                yield _sse(event_name, {**payload, "sequence": sequence})
                pending = asyncio.ensure_future(iterator.__anext__())
        except KeyError:
            sequence += 1
            yield _sse("error", {"sequence": sequence, "code": "SESSION_NOT_FOUND"})
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            await service.release_analysis_lease(session_id, lease_owner)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/sessions/{session_id}")
async def read_session(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> StreamingResponse:
    key_hash: str | None = None
    replay: list[dict[str, object]] | None = None
    if idempotency_key is not None:
        if not idempotency_key.strip() or len(idempotency_key) > 256:
            raise ApiProblem(
                422,
                "INVALID_INPUT",
                "Invalid idempotency key",
                "Idempotency-Key must contain 1 to 256 characters.",
            )
        key_hash = hashlib.sha256(f"{session_id}:{idempotency_key}".encode()).hexdigest()
        claim_status, stored = await service.begin_answer_idempotency(session_id, key_hash)
        if claim_status == "IN_PROGRESS":
            raise ApiProblem(
                409,
                "SESSION_BUSY",
                "Answer is already being processed",
                "A request with this Idempotency-Key is still in progress.",
                retryable=True,
            )
        if claim_status == "COMPLETED":
            replay = stored or []
    if replay is None:
        try:
            await enforce_rate_limit(request, "answer_submission")
        except Exception:
            if key_hash is not None:
                await service.abandon_answer_idempotency(session_id, key_hash)
            raise

    async def stream() -> AsyncIterator[bytes]:
        if replay is not None:
            for item in replay:
                stored_payload = item.get("payload_json")
                if not isinstance(stored_payload, str):
                    raise RuntimeError("stored idempotency response is malformed")
                yield _sse_json(str(item["event_name"]), stored_payload)
            return
        sequence = 0
        captured: list[dict[str, object]] = []
        finished = False
        try:
            async for event_name, payload in service.submit_answer(
                session_id,
                question_id=body.question_id,
                answer_text=body.answer_text,
                structured_value=body.structured_value,
                unknown=body.unknown,
                declined=body.declined,
            ):
                sequence += 1
                numbered = {**payload, "sequence": sequence}
                payload_json = orjson.dumps(numbered).decode()
                captured.append({"event_name": event_name, "payload_json": payload_json})
                yield _sse_json(event_name, payload_json)
        except ValueError as error:
            numbered = {"sequence": sequence + 1, "code": str(error)}
            payload_json = orjson.dumps(numbered).decode()
            captured.append({"event_name": "error", "payload_json": payload_json})
            yield _sse_json("error", payload_json)
            finished = True
        else:
            finished = True
        finally:
            if key_hash is not None:
                if finished:
                    await service.complete_answer_idempotency(session_id, key_hash, captured)
                else:
                    await service.abandon_answer_idempotency(session_id, key_hash)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/sessions/{session_id}/trials/{nct_id}/proof")
async def read_proof(
    session_id: str,
    nct_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    payload = await service.read_proof(session_id, nct_id)
    if payload is None:
        raise ApiProblem(404, "SESSION_NOT_FOUND", "Proof not found", "The proof is not available.")
    return payload


@router.get("/sessions/{session_id}/export")
@router.get("/sessions/{session_id}/export.json")
async def export_report(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    session = await service.read_session(session_id)
    if session is not None and session.get("export_available") is False:
        raise ApiProblem(
            503,
            "EXPORT_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Export unavailable",
            "Persistence failed during this request; the volatile result is not durable.",
            retryable=False,
        )
    try:
        payload = await service.export_report(session_id)
    except PersistenceUnavailableError as error:
        raise ApiProblem(
            503,
            "EXPORT_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Export unavailable",
            "Persistence failed during export; no durable artifact was created.",
            retryable=False,
        ) from error
    if payload is None:
        raise ApiProblem(
            404,
            "SESSION_NOT_FOUND",
            "Report not found",
            "The report is not available.",
        )
    return payload


@router.get("/sessions/{session_id}/report", response_class=HTMLResponse)
async def printable_report(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    session = await service.read_session(session_id)
    if session is not None and session.get("export_available") is False:
        raise ApiProblem(
            503,
            "EXPORT_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Printable report unavailable",
            "Persistence failed during this request; the volatile result is not durable.",
            retryable=False,
        )
    try:
        payload = await service.export_report(session_id)
    except PersistenceUnavailableError as error:
        raise ApiProblem(
            503,
            "EXPORT_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Printable report unavailable",
            "Persistence failed during report creation.",
            retryable=False,
        ) from error
    if payload is None:
        raise ApiProblem(404, "SESSION_NOT_FOUND", "Report not found", "Report unavailable.")
    encoded = escape(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><title>TRIAL-OPT report</title>"
        "<style>body{font:14px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f5;padding:1rem;}"
        "@media print{body{margin:0;}}</style></head><body><h1>TRIAL-OPT</h1>"
        "<p>Research pre-screening only; not diagnosis, medical advice, or final eligibility.</p>"
        f"<pre>{encoded}</pre></body></html>"
    )


@router.post("/sessions/{session_id}/reset", status_code=status.HTTP_201_CREATED)
async def reset_session(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    session = await service.read_session(session_id)
    if session is not None and session.get("durable_replay") is False:
        raise ApiProblem(
            503,
            "RESET_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Reset unavailable",
            "The volatile session cannot create a durable replay chain.",
            retryable=False,
        )
    try:
        return await service.reset_session(session_id)
    except KeyError as error:
        raise ApiProblem(
            404, "SESSION_NOT_FOUND", "Session not found", "The session does not exist."
        ) from error


@router.delete("/sessions/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_session(
    session_id: str,
    _: str = Depends(require_session_token),
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        deleted = await service.delete_session(session_id)
    except PersistenceUnavailableError as error:
        raise ApiProblem(
            503,
            "DELETE_UNAVAILABLE_PERSISTENCE_DEGRADED",
            "Deletion unavailable",
            "Durable deletion could not be confirmed while persistence is degraded.",
            retryable=True,
        ) from error
    if not deleted:
        raise ApiProblem(
            404, "SESSION_NOT_FOUND", "Session not found", "The session does not exist."
        )
    return {"status": "accepted", "session_id": session_id, "cleanup_queued": True}
