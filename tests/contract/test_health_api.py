from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.settings import get_settings


def test_health_has_no_external_dependency_requirement() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["X-Request-Id"]
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["gemini_circuit"] == "closed"
    assert payload["checks"]["firestore"] == "unknown"
    assert len(payload["config_hash"]) == 64


def test_public_config_reports_the_deployed_snapshot_version(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_SNAPSHOT_VERSION", "qa-snapshot-v2")
    get_settings.cache_clear()
    try:
        response = TestClient(app).get("/api/v1/config/public")
        assert response.status_code == 200
        assert response.json()["snapshot_version"] == "qa-snapshot-v2"
    finally:
        get_settings.cache_clear()
