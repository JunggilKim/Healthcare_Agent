from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from backend.app.api.middleware import request_id_middleware
from backend.app.main import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_declared_spa_routes_support_direct_navigation() -> None:
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200
    assert client.get("/about").status_code == 200
    assert client.head("/about").status_code == 200
    assert client.get("/session/session-example").status_code == 200
    assert client.head("/session/session-example").status_code == 200
    assert client.get("/api/v1/not-a-route").status_code == 404
    assert client.get("/unrecognized-frontend-route").status_code == 404

    source_index = (REPOSITORY_ROOT / "frontend" / "index.html").read_text()
    assert 'href="/favicon.svg"' in source_index
    assert (REPOSITORY_ROOT / "frontend" / "public" / "favicon.svg").is_file()


def test_frontend_cache_and_compression_contracts() -> None:
    client = TestClient(app)
    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"

    asset_app = FastAPI()
    asset_app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    asset_app.middleware("http")(request_id_middleware)

    @asset_app.get("/assets/test-hash.js")
    async def static_asset() -> Response:
        return Response("x" * 2000, media_type="application/javascript")

    asset = TestClient(asset_app).get("/assets/test-hash.js", headers={"Accept-Encoding": "gzip"})
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["content-encoding"] == "gzip"
