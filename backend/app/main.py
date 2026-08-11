from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.middleware import request_id_middleware
from backend.app.api.routes.health import router as health_router

DISCLAIMER = (
    "This system is a research prototype for clinical-trial pre-screening using public and "
    "synthetic data. It does not diagnose disease, provide medical advice, determine final "
    "eligibility, or replace review by a qualified clinical-trial team."
)

app = FastAPI(
    title="TRIAL-OPT",
    version="0.1.0",
    description="Proof-carrying active evidence acquisition for clinical-trial pre-screening.",
)
app.middleware("http")(request_id_middleware)
app.include_router(health_router, prefix="/api/v1")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
if (_STATIC_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="spa")
else:

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def phase_zero_landing() -> str:
        return f"""<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><title>TRIAL-OPT</title></head>
<body><main><h1>TRIAL-OPT</h1>
<p>근거 증명형 능동 정보 획득 기반 임상시험 사전 선별 연구 프로토타입</p>
<p>{DISCLAIMER}</p></main></body></html>"""
