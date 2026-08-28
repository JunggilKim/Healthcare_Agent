from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

CASE_IDS = tuple(f"S{index:03d}" for index in range(1, 11))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise the S001-S010 Snapshot and Live API matrix."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--modes",
        default="snapshot,live",
        help="comma-separated subset of snapshot,live",
    )
    parser.add_argument(
        "--cases",
        default=",".join(CASE_IDS),
        help="comma-separated seed case identifiers",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser.parse_args()


def _problem(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"status": response.status_code, "detail": response.text[:500]}
    return {
        "status": response.status_code,
        "code": payload.get("code") or payload.get("type"),
        "title": payload.get("title"),
        "detail": payload.get("detail"),
    }


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    full_state = session.get("full_state") or {}
    aggregate = full_state.get("aggregate") or {}
    ranked_ids = aggregate.get("ranked_nct_ids") or session.get("ranked_nct_ids") or []
    top_id = ranked_ids[0] if ranked_ids else None
    evaluations = aggregate.get("trial_evaluations") or {}
    compiled_trials = aggregate.get("compiled_trials") or {}
    top_evaluation = evaluations.get(top_id, {}) if top_id else {}
    top_compiled = compiled_trials.get(top_id, {}) if top_id else {}
    top_criteria = top_compiled.get("criteria") or []
    selection = session.get("current_question") or {}
    retrieval = session.get("retrieval") or {}
    retrieval_candidates = retrieval.get("ranked_candidates") or []
    return {
        "state": session.get("state"),
        "engine": session.get("engine"),
        "mode": session.get("mode"),
        "support_level": session.get("support_level", "full_evaluation"),
        "degradation_codes": session.get("degradation_codes") or [],
        "retrieved_count": len(retrieval_candidates),
        "retrieval_top_nct_ids": [
            item.get("nct_id") for item in retrieval_candidates[:3]
        ],
        "retrieval_top_titles": [
            (item.get("trial") or {}).get("brief_title")
            for item in retrieval_candidates[:3]
        ],
        "selected_for_compilation": retrieval.get("selected_for_compilation") or [],
        "reviewed_protocol_reuse_ids": session.get("reviewed_protocol_reuse_ids") or [],
        "ranked_nct_ids": ranked_ids,
        "top_nct_id": top_id,
        "top_decision": top_evaluation.get("decision"),
        "top_display_score": top_evaluation.get("display_score"),
        "top_proof_completeness": top_evaluation.get("proof_completeness"),
        "top_opaque_critical_count": top_evaluation.get("opaque_critical_count"),
        "top_degradation_codes": top_evaluation.get("degradation_codes") or [],
        "top_protocol_verified": top_compiled.get("protocol_verified"),
        "top_criterion_count": len(top_criteria),
        "top_opaque_count": sum(bool(item.get("opaque")) for item in top_criteria),
        "question_id": (selection.get("selected") or {}).get("question_id"),
        "question_slot_id": (selection.get("selected") or {}).get("slot_id"),
        "stop_reason": selection.get("stop_reason"),
        "stop_rationale": selection.get("deterministic_rationale"),
    }


def _run_case(
    client: httpx.Client,
    *,
    base_url: str,
    mode: str,
    case_id: str,
    evaluation_date: str,
) -> dict[str, Any]:
    started = time.monotonic()
    create = client.post(
        f"{base_url}/api/v1/sessions",
        json={
            "mode": mode,
            "seed_case_id": case_id,
            "evaluation_date": evaluation_date,
            "language": "en",
            "confirm_synthetic_public": False,
            "identifier_warning_acknowledged": False,
        },
    )
    if create.status_code != 201:
        return {
            "case_id": case_id,
            "mode": mode,
            "phase": "create",
            "latency_seconds": round(time.monotonic() - started, 3),
            "problem": _problem(create),
        }
    credentials = create.json()
    headers = {
        "X-Session-Token": credentials["session_token"],
        "Accept": "text/event-stream",
    }
    analysis_started = time.monotonic()
    analysis = client.post(
        f"{base_url}/api/v1/sessions/{credentials['session_id']}/analysis",
        headers=headers,
    )
    analysis_latency = round(time.monotonic() - analysis_started, 3)
    if analysis.status_code != 200:
        return {
            "case_id": case_id,
            "mode": mode,
            "phase": "analysis",
            "session_id": credentials["session_id"],
            "analysis_latency_seconds": analysis_latency,
            "problem": _problem(analysis),
        }
    event_names = [
        line.removeprefix("event: ")
        for line in analysis.text.splitlines()
        if line.startswith("event: ")
    ]
    read = client.get(
        f"{base_url}/api/v1/sessions/{credentials['session_id']}",
        headers={"X-Session-Token": credentials["session_token"]},
    )
    if read.status_code != 200:
        return {
            "case_id": case_id,
            "mode": mode,
            "phase": "read",
            "session_id": credentials["session_id"],
            "analysis_latency_seconds": analysis_latency,
            "events": event_names,
            "problem": _problem(read),
        }
    return {
        "case_id": case_id,
        "mode": mode,
        "phase": "completed",
        "session_id": credentials["session_id"],
        "analysis_latency_seconds": analysis_latency,
        "total_latency_seconds": round(time.monotonic() - started, 3),
        "events": event_names,
        **_session_summary(read.json()),
    }


def main() -> None:
    args = _parse_args()
    base_url = args.base_url.rstrip("/")
    modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
    cases = tuple(item.strip() for item in args.cases.split(",") if item.strip())
    if not set(modes) <= {"snapshot", "live"}:
        raise SystemExit("--modes must contain only snapshot and live")
    if not cases or not set(cases) <= set(CASE_IDS):
        raise SystemExit("--cases must contain only S001-S010")
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=args.timeout_seconds) as client:
        config_response = client.get(f"{base_url}/api/v1/config/public")
        config_response.raise_for_status()
        config = config_response.json()
        for mode in modes:
            evaluation_date = (
                str(config["snapshot_data_date"])
                if mode == "snapshot"
                else date.today().isoformat()
            )
            for case_id in cases:
                print(f"START {mode} {case_id}", flush=True)
                row = _run_case(
                    client,
                    base_url=base_url,
                    mode=mode,
                    case_id=case_id,
                    evaluation_date=evaluation_date,
                )
                rows.append(row)
                print(
                    "RESULT "
                    + json.dumps(
                        {
                            "mode": mode,
                            "case_id": case_id,
                            "phase": row.get("phase"),
                            "latency": row.get("analysis_latency_seconds"),
                            "top": row.get("top_nct_id"),
                            "decision": row.get("top_decision"),
                            "question": row.get("question_slot_id") or row.get("stop_reason"),
                            "degradations": row.get("degradation_codes"),
                            "problem": row.get("problem"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
    report = {
        "schema_version": "trial-opt-demo-matrix-audit-v1",
        "base_url": base_url,
        "generated_on": date.today().isoformat(),
        "rows": rows,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
