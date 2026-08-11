from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.annotations import (  # noqa: E402
    AnnotationAssignment,
    AnnotationVerdict,
    load_jsonl,
)
from backend.app.evaluation.proof_baselines import (  # noqa: E402
    ProofBaselineEvidence,
    ProofBaselinePrediction,
)

MODEL_ID = "gemini-3.6-flash"
PROMPT_VERSION = "proof-baseline-v1"


def _rows(paths: list[Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in paths:
        for line_number, raw in enumerate(path.read_bytes().splitlines(), start=1):
            if not raw.strip():
                continue
            row = orjson.loads(raw)
            if not isinstance(row, dict):
                raise ValueError(f"PROOF_BASELINE_RESPONSE_ROW_INVALID:{path}:{line_number}")
            result.append(row)
    return result


def _response_text(row: dict[str, Any]) -> str:
    if row.get("error"):
        raise ValueError(f"PROOF_BASELINE_BATCH_ERROR:{row.get('id')}:{row['error']}")
    response = row.get("response")
    candidates = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError("PROOF_BASELINE_RESPONSE_CANDIDATE_INVALID")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    text = parts[0].get("text") if isinstance(parts, list) and parts else None
    if not isinstance(text, str) or not text:
        raise ValueError("PROOF_BASELINE_RESPONSE_TEXT_MISSING")
    return text


def _assignments(path: Path) -> list[AnnotationAssignment]:
    return [
        AnnotationAssignment.model_validate(item.model_dump(mode="json"))
        for item in load_jsonl(path, AnnotationAssignment)
    ]


def _prepare(args: argparse.Namespace) -> None:
    assignments = _assignments(args.assignments)
    template = (REPOSITORY_ROOT / "prompts/proof_baseline_v1.md").read_text(encoding="utf-8")
    schema = {
        "type": "OBJECT",
        "properties": {
            "p0_verdict": {"type": "STRING", "enum": [item.value for item in AnnotationVerdict]},
            "p0_explanation": {"type": "STRING"},
            "p1_verdict": {"type": "STRING", "enum": [item.value for item in AnnotationVerdict]},
            "p1_evidence_fact_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "p1_explanation": {"type": "STRING"},
        },
        "required": [
            "p0_verdict",
            "p0_explanation",
            "p1_verdict",
            "p1_evidence_fact_ids",
            "p1_explanation",
        ],
    }
    requests = []
    prompt_hashes = {}
    for assignment in assignments:
        prompt = (
            template.replace("{criterion_source}", assignment.source_span.quote)
            .replace("{source_direction}", assignment.source_direction.value)
            .replace("{patient_narrative}", assignment.narrative)
            .replace(
                "{structured_facts_json}",
                canonical_json_bytes(
                    [fact.model_dump(mode="json") for fact in assignment.facts]
                ).decode(),
            )
            .replace(
                "{conflict_slots_json}", canonical_json_bytes(assignment.conflict_slots).decode()
            )
        )
        prompt_hashes[assignment.record_id] = hashlib.sha256(prompt.encode()).hexdigest()
        requests.append(
            {
                "id": assignment.record_id,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0,
                        "maxOutputTokens": 1200,
                        "responseMimeType": "application/json",
                        "responseSchema": schema,
                    },
                },
            }
        )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    request_path = output / "proof_baseline_requests.jsonl"
    request_path.write_bytes(b"\n".join(canonical_json_bytes(row) for row in requests) + b"\n")
    manifest = {
        "schema_version": "trial-opt-proof-baseline-input-v1",
        "annotation_manifest_sha256": hashlib.sha256(
            args.annotation_manifest.read_bytes()
        ).hexdigest(),
        "assignment_jsonl_sha256": hashlib.sha256(args.assignments.read_bytes()).hexdigest(),
        "request_jsonl_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "prompt_template_sha256": hashlib.sha256(
            (REPOSITORY_ROOT / "prompts/proof_baseline_v1.md").read_bytes()
        ).hexdigest(),
        "prompt_hashes": prompt_hashes,
        "record_count": len(assignments),
    }
    (output / "input_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), "record_count": len(assignments)}).decode())


def _finalize(args: argparse.Namespace) -> None:
    assignments = _assignments(args.assignments)
    assignment_by_id = {item.record_id: item for item in assignments}
    input_manifest = orjson.loads(args.input_manifest.read_bytes())
    if (
        input_manifest.get("assignment_jsonl_sha256")
        != hashlib.sha256(args.assignments.read_bytes()).hexdigest()
    ):
        raise RuntimeError("PROOF_BASELINE_INPUT_ASSIGNMENT_HASH_MISMATCH")
    responses = _rows(args.responses)
    by_id = {row.get("id"): row for row in responses}
    if set(by_id) != set(assignment_by_id) or len(by_id) != len(responses):
        raise RuntimeError("PROOF_BASELINE_RESPONSE_COVERAGE_MISMATCH")
    predictions = []
    for record_id, assignment in assignment_by_id.items():
        text = _response_text(by_id[record_id])
        payload = json.loads(text)
        if set(payload) != {
            "p0_verdict",
            "p0_explanation",
            "p1_verdict",
            "p1_evidence_fact_ids",
            "p1_explanation",
        }:
            raise RuntimeError(f"PROOF_BASELINE_RESPONSE_SCHEMA_INVALID:{record_id}")
        if not set(payload["p1_evidence_fact_ids"]).issubset(
            {fact.fact_id for fact in assignment.facts}
        ):
            raise RuntimeError(f"PROOF_BASELINE_EVIDENCE_FACT_UNKNOWN:{record_id}")
        predictions.append(
            ProofBaselinePrediction(
                record_id=record_id,
                assignment_hash=assignment.assignment_hash,
                p0_verdict=payload["p0_verdict"],
                p0_explanation=payload["p0_explanation"],
                p1_verdict=payload["p1_verdict"],
                p1_evidence_fact_ids=payload["p1_evidence_fact_ids"],
                p1_explanation=payload["p1_explanation"],
                prompt_sha256=input_manifest["prompt_hashes"][record_id],
                response_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    evidence = ProofBaselineEvidence(
        status="BATCH_COMPLETED",
        annotation_manifest_sha256=hashlib.sha256(
            args.annotation_manifest.read_bytes()
        ).hexdigest(),
        assignment_jsonl_sha256=hashlib.sha256(args.assignments.read_bytes()).hexdigest(),
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSION,
        batch_job_name=args.batch_job_name,
        completed_at=datetime.fromisoformat(args.completed_at),
        predictions=predictions,
    )
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence.model_dump(mode="json")))
    print(orjson.dumps({"output": str(args.output), "records": len(predictions)}).decode())


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare/finalize paid P0/P1 batch baselines")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--annotation-manifest", type=Path, required=True)
    prepare.add_argument("--assignments", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--annotation-manifest", type=Path, required=True)
    finalize.add_argument("--assignments", type=Path, required=True)
    finalize.add_argument("--input-manifest", type=Path, required=True)
    finalize.add_argument("--responses", type=Path, action="append", required=True)
    finalize.add_argument("--batch-job-name", required=True)
    finalize.add_argument("--completed-at", required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.set_defaults(handler=_finalize)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
