from __future__ import annotations

import math
import time

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.settings import get_settings


def _create_and_analyze(client: TestClient) -> tuple[str, dict[str, str], dict[str, object]]:
    created = client.post(
        "/api/v1/sessions",
        json={
            "mode": "snapshot",
            "seed_case_id": "S004",
            "evaluation_date": "2026-08-11",
            "language": "en",
            "confirm_synthetic_public": False,
            "identifier_warning_acknowledged": False,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    headers = {
        "X-Session-Token": created.json()["session_token"],
        "Accept": "text/event-stream",
    }
    with client.stream(
        "POST", f"/api/v1/sessions/{session_id}/analysis", headers=headers
    ) as response:
        assert response.status_code == 200
        analysis_stream = "".join(response.iter_text())
    assert "event: question_selected" in analysis_stream
    initial = client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()
    return session_id, headers, initial


def test_s004_snapshot_api_vertical_slice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    with TestClient(app) as client:
        session_id, headers, initial = _create_and_analyze(client)
        current_question = initial["current_question"]
        assert current_question["selected"]["slot_id"] == "pathology.histology"
        proofs = {item["criterion_id"]: item for item in initial["proofs"]}
        assert proofs["NCT05239624:INCLUSION:001:443174ab"]["final_verdict"] == "PASS"
        assert proofs["NCT05239624:INCLUSION:002:5f52ab88"]["final_verdict"] == "UNKNOWN"

        with client.stream(
            "POST",
            f"/api/v1/sessions/{session_id}/answers",
            headers=headers,
            json={
                "question_id": current_question["selected"]["question_id"],
                "answer_text": (
                    "Existing pathology report confirms high-grade urothelial carcinoma."
                ),
                "structured_value": None,
                "unknown": False,
                "declined": False,
            },
        ) as response:
            assert response.status_code == 200
            answer_stream = "".join(response.iter_text())
        assert '"slot_id":"pathology.muscle_invasion"' in answer_stream

        updated = client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()
        updated_proofs = {item["criterion_id"]: item for item in updated["proofs"]}
        assert updated_proofs["NCT05239624:INCLUSION:002:5f52ab88"]["final_verdict"] == "PASS"
        assert updated_proofs["NCT05239624:INCLUSION:003:a7db6608"]["final_verdict"] == "UNKNOWN"
        assert updated["current_question"]["selected"]["slot_id"] == "pathology.muscle_invasion"

        proof = client.get(
            f"/api/v1/sessions/{session_id}/trials/NCT05239624/proof", headers=headers
        )
        assert proof.status_code == 200
        assert len(proof.json()["proof_packets"]) == 7
        assert all(
            next(check for check in packet["verifier_checks"] if check["check_id"] == "PV-012")[
                "passed"
            ]
            for packet in proof.json()["proof_packets"]
        )
        exported = client.get(f"/api/v1/sessions/{session_id}/export", headers=headers)
        assert exported.status_code == 200
        export_payload = exported.json()
        assert export_payload["report"]["source"] == "DETERMINISTIC_TEMPLATE"
        assert export_payload["estimated_cost_usd"] == 0.0
        assert len(export_payload["artifact_sha256"]) == 64
    get_settings.cache_clear()


def test_arbitrary_input_identifier_warning_precedes_snapshot_unavailable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_STORE_DIR", str(tmp_path / "pii-store"))
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            json={
                "mode": "snapshot",
                "patient_text": "name: Synthetic Person, demo@example.org",
                "evaluation_date": "2026-08-11",
                "confirm_synthetic_public": True,
                "identifier_warning_acknowledged": False,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "PII_WARNING_REQUIRED"
        assert "demo@example.org" not in response.json()["detail"]
        acknowledged = client.post(
            "/api/v1/sessions",
            json={
                "mode": "snapshot",
                "patient_text": "name: Synthetic Person, demo@example.org",
                "evaluation_date": "2026-08-11",
                "confirm_synthetic_public": True,
                "identifier_warning_acknowledged": True,
            },
        )
        assert acknowledged.status_code == 422
        assert acknowledged.json()["code"] == "SNAPSHOT_BRANCH_UNAVAILABLE"
    get_settings.cache_clear()


def test_s004_branch_b_keeps_histology_unknown_and_never_repeats_question(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCAL_STORE_DIR", str(tmp_path / "branch-b-store"))
    get_settings.cache_clear()
    with TestClient(app) as client:
        session_id, headers, initial = _create_and_analyze(client)
        question_id = initial["current_question"]["selected"]["question_id"]
        with client.stream(
            "POST",
            f"/api/v1/sessions/{session_id}/answers",
            headers=headers,
            json={
                "question_id": question_id,
                "answer_text": (
                    "No pathology test has been performed; only the CT finding is available."
                ),
                "structured_value": None,
                "unknown": False,
                "declined": False,
            },
        ) as response:
            assert response.status_code == 200
            _ = "".join(response.iter_text())
        updated = client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()
        histology = next(
            proof
            for proof in updated["proofs"]
            if proof["criterion_id"] == "NCT05239624:INCLUSION:002:5f52ab88"
        )
        assert histology["final_verdict"] == "UNKNOWN"
        assert "pathology.histology" in updated["unavailable_slot_ids"]
        assert updated["current_question"]["selected"]["slot_id"] == "pathology.muscle_invasion"
    get_settings.cache_clear()


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def test_snapshot_latency_gates_across_twenty_local_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_STORE_DIR", str(tmp_path / "latency-store"))
    get_settings.cache_clear()
    initial_latencies: list[float] = []
    answer_latencies: list[float] = []
    with TestClient(app) as client:
        for _ in range(20):
            started = time.perf_counter()
            session_id, headers, initial = _create_and_analyze(client)
            initial_latencies.append(time.perf_counter() - started)
            question = initial["current_question"]["selected"]
            started = time.perf_counter()
            with client.stream(
                "POST",
                f"/api/v1/sessions/{session_id}/answers",
                headers=headers,
                json={
                    "question_id": question["question_id"],
                    "answer_text": None,
                    "structured_value": None,
                    "unknown": True,
                    "declined": False,
                },
            ) as response:
                assert response.status_code == 200
                _ = "".join(response.iter_text())
            answer_latencies.append(time.perf_counter() - started)
    assert _p95(initial_latencies) < 3.0
    assert _p95(answer_latencies) < 1.0
    get_settings.cache_clear()
