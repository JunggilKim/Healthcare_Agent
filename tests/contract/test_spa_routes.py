from fastapi.testclient import TestClient

from backend.app.main import app


def test_declared_spa_routes_support_direct_navigation() -> None:
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/about").status_code == 200
    assert client.get("/session/session-example").status_code == 200
    assert client.get("/api/v1/not-a-route").status_code == 404
    assert client.get("/unrecognized-frontend-route").status_code == 404
