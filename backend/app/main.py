from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.api.errors import ApiProblem, problem_handler
from backend.app.api.middleware import request_id_middleware
from backend.app.api.routes.config import router as config_router
from backend.app.api.routes.demo import router as demo_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.sessions import router as sessions_router
from backend.app.application.session_service import SnapshotSessionService
from backend.app.infrastructure.local_store import LocalSessionStore
from backend.app.infrastructure.rate_limiter import FixedWindowRateLimiter
from backend.app.main_constants import DISCLAIMER
from backend.app.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    store = LocalSessionStore(settings.local_store_dir)
    await store.initialize()
    application.state.session_service = SnapshotSessionService(store)
    application.state.rate_limiter = FixedWindowRateLimiter(salt=settings.ip_hash_salt)
    yield


app = FastAPI(
    title="TRIAL-OPT",
    version="0.1.0",
    description="Proof-carrying active evidence acquisition for clinical-trial pre-screening.",
    lifespan=lifespan,
)
app.middleware("http")(request_id_middleware)
app.add_exception_handler(ApiProblem, problem_handler)
app.include_router(health_router, prefix="/api/v1")
app.include_router(config_router, prefix="/api/v1")
app.include_router(demo_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_FILE = _STATIC_DIR / "index.html"
if (_STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="spa-assets")


def _phase_zero_html() -> str:
    return f"""<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><title>TRIAL-OPT</title></head>
<body><main><h1>TRIAL-OPT</h1>
<p>근거 증명형 능동 정보 획득 기반 임상시험 사전 선별 연구 프로토타입</p>
<p>{DISCLAIMER}</p></main></body></html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def spa_root() -> Response:
    return FileResponse(_INDEX_FILE) if _INDEX_FILE.is_file() else HTMLResponse(_phase_zero_html())


@app.get("/{frontend_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_route(frontend_path: str) -> Response:
    candidate = (_STATIC_DIR / frontend_path).resolve()
    if _STATIC_DIR.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    if frontend_path == "about" or (
        frontend_path.startswith("session/") and len(frontend_path.split("/")) == 2
    ):
        return (
            FileResponse(_INDEX_FILE) if _INDEX_FILE.is_file() else HTMLResponse(_phase_zero_html())
        )
    raise HTTPException(status_code=404, detail="Not Found")
