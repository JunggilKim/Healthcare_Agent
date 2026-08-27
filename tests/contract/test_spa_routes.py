from fastapi.testclient import TestClient

from backend.app.main import app


def test_declared_spa_routes_support_direct_navigation() -> None:
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/about").status_code == 200
    assert client.get("/session/session-example").status_code == 200
    assert client.get("/api/v1/not-a-route").status_code == 404
    assert client.get("/unrecognized-frontend-route").status_code == 404


def test_frontend_cache_and_compression_contracts() -> None:
    client = TestClient(app)
    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"

    asset_path = next(
        value.split('"')[0]
        for value in index.text.split('src="')[1:]
        if value.startswith("/assets/")
    )
    asset = client.get(asset_path, headers={"Accept-Encoding": "gzip"})
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["content-encoding"] == "gzip"
