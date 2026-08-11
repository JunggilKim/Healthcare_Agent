from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.settings import get_settings


def _frames(stream: str) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in stream.strip().split("\n\n"):
        lines = dict(line.split(": ", maxsplit=1) for line in frame.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_analysis_sse_has_strictly_increasing_sequence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_STORE_DIR", str(tmp_path / "sse-store"))
    get_settings.cache_clear()
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "mode": "snapshot",
                "seed_case_id": "S004",
                "evaluation_date": "2026-08-11",
                "language": "en",
            },
        )
        assert created.status_code == 201
        credentials = created.json()
        with client.stream(
            "POST",
            f"/api/v1/sessions/{credentials['session_id']}/analysis",
            headers={"X-Session-Token": credentials["session_token"]},
        ) as response:
            assert response.status_code == 200
            events = _frames("".join(response.iter_text()))

    sequences = [int(payload["sequence"]) for _, payload in events]
    assert sequences == list(range(1, len(events) + 1))
    assert events[-1][0] == "completed"
    get_settings.cache_clear()
