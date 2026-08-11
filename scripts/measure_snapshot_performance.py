from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

os.environ["APP_ENV"] = "local"
os.environ["STORE_BACKEND"] = "local"
os.environ["ALLOW_LIVE_MODEL_CALLS"] = "false"
os.environ["ALLOW_LIVE_CTGOV_CALLS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.application.vertical_slice import load_vertical_slice  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.performance_evidence import percentile_nearest_rank  # noqa: E402
from backend.app.main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure 20+ actual local Snapshot HTTP analysis and answer runs"
    )
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 20:
        raise SystemExit("Snapshot release performance requires at least 20 runs")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_loggers = [
        logging.getLogger(name)
        for name in ("trial_opt.request", "trial_opt.model", "trial_opt.persistence")
    ]
    for logger in audit_loggers:
        logger.addHandler(handler)
    initial_durations = []
    answer_durations = []
    try:
        with TestClient(app) as client:
            for _ in range(args.runs):
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
                created.raise_for_status()
                payload = created.json()
                session_id = payload["session_id"]
                headers = {"X-Session-Token": payload["session_token"]}
                started = time.perf_counter()
                analysis = client.post(
                    f"/api/v1/sessions/{session_id}/analysis",
                    headers={**headers, "Accept": "text/event-stream"},
                )
                initial_durations.append(time.perf_counter() - started)
                analysis.raise_for_status()
                if "event: completed" not in analysis.text:
                    raise RuntimeError("SNAPSHOT_PERFORMANCE_ANALYSIS_NOT_COMPLETED")
                session = client.get(f"/api/v1/sessions/{session_id}", headers=headers)
                session.raise_for_status()
                question_id = session.json()["current_question"]["selected"]["question_id"]
                started = time.perf_counter()
                answer = client.post(
                    f"/api/v1/sessions/{session_id}/answers",
                    headers={**headers, "Accept": "text/event-stream"},
                    json={
                        "question_id": question_id,
                        "answer_text": None,
                        "structured_value": None,
                        "unknown": True,
                        "declined": False,
                    },
                )
                answer_durations.append(time.perf_counter() - started)
                answer.raise_for_status()
                if "event: completed" not in answer.text:
                    raise RuntimeError("SNAPSHOT_PERFORMANCE_ANSWER_NOT_COMPLETED")
    finally:
        for logger in audit_loggers:
            logger.removeHandler(handler)
    captured_logs = log_stream.getvalue()
    raw_patient = load_vertical_slice().patient_text
    raw_occurrences = captured_logs.count(raw_patient)
    document = {
        "schema_version": "trial-opt-snapshot-performance-v1",
        "status": "SNAPSHOT_MEASURED_EXTERNAL_PENDING",
        "source_git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "measured_at": datetime.now(UTC).isoformat(),
        "run_count": args.runs,
        "snapshot_initial_analysis_seconds": initial_durations,
        "snapshot_initial_analysis_p95_seconds": percentile_nearest_rank(initial_durations, 0.95),
        "snapshot_answer_reevaluation_seconds": answer_durations,
        "snapshot_answer_reevaluation_p95_seconds": percentile_nearest_rank(answer_durations, 0.95),
        "raw_patient_text_log_occurrences": raw_occurrences,
        "captured_structured_log_sha256": hashlib.sha256(captured_logs.encode()).hexdigest(),
        "pending": [
            "warm-cache live 20 runs",
            "cold live 10 runs",
            "live answer reevaluation",
            "dependency-failure fallback",
            "Cloud Run container startup health",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(document))
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "initial_p95": document["snapshot_initial_analysis_p95_seconds"],
                "answer_p95": document["snapshot_answer_reevaluation_p95_seconds"],
                "raw_patient_text_log_occurrences": raw_occurrences,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
