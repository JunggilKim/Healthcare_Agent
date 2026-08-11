from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_has_no_external_dependency_requirement() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Request-Id"]
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["gemini_circuit"] == "closed"
    assert payload["checks"]["firestore"] == "unknown"
    assert len(payload["config_hash"]) == 64
