from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.agents.patient_evidence import compact_patient_slot_catalog  # noqa: E402
from backend.app.agents.prompts import render_prompt  # noqa: E402
from backend.app.application.catalog import load_slot_catalog  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.domain.model_outputs import PatientExtractionResult  # noqa: E402
from backend.app.evaluation.models import BenchmarkArtifact  # noqa: E402
from backend.app.evaluation.paraphrases import (  # noqa: E402
    EXTRACTION_MODEL_ID,
    PARAPHRASE_MODEL_ID,
    PARAPHRASE_PROMPT_VERSION,
    SelectedWorld,
    apply_validated_paraphrases,
    build_extraction_requests,
    build_paraphrase_requests,
    paraphrase_candidate_worlds,
    parse_extraction_responses,
    parse_paraphrase_responses,
    select_paraphrase_worlds,
    validate_paraphrase_spans,
)
from backend.app.infrastructure.cache import LocalModelResultCache  # noqa: E402
from backend.app.infrastructure.circuit_breaker import CircuitOpenError  # noqa: E402
from backend.app.infrastructure.genai_client import create_google_cloud_genai_client  # noqa: E402
from backend.app.infrastructure.structured_generation import (  # noqa: E402
    StructuredGenerationUnavailable,
    StructuredGenerator,
)
from backend.app.infrastructure.usage_guard import (  # noqa: E402
    InMemoryUsageGuard,
    default_pricing_estimator,
)
from backend.app.settings import Settings  # noqa: E402


def _jsonl_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line_number, raw in enumerate(path.read_bytes().splitlines(), start=1):
            if not raw.strip():
                continue
            item = orjson.loads(raw)
            if not isinstance(item, dict):
                raise ValueError(f"BATCH_JSONL_ROW_INVALID:{path}:{line_number}")
            rows.append(item)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n".join(canonical_json_bytes(row) for row in rows) + b"\n")


def _load_benchmark(path: Path) -> BenchmarkArtifact:
    return BenchmarkArtifact.model_validate(orjson.loads(path.read_bytes()))


def _load_selection(path: Path, benchmark_path: Path) -> list[SelectedWorld]:
    manifest = orjson.loads(path.read_bytes())
    if manifest.get("benchmark_sha256") != hashlib.sha256(benchmark_path.read_bytes()).hexdigest():
        raise RuntimeError("PARAPHRASE_SELECTION_BENCHMARK_HASH_MISMATCH")
    return [SelectedWorld(**item) for item in manifest["selected_worlds"]]


def _prepare(args: argparse.Namespace) -> None:
    benchmark = _load_benchmark(args.benchmark)
    prompt_path = REPOSITORY_ROOT / "prompts" / "synthetic_paraphrase_v1.md"
    candidates = paraphrase_candidate_worlds(benchmark)
    target_count = len(select_paraphrase_worlds(benchmark))
    candidate_count = args.candidate_count or target_count
    if args.candidate_offset < 0 or candidate_count < 1:
        raise RuntimeError("PARAPHRASE_CANDIDATE_RANGE_INVALID")
    selected_candidates = candidates[
        args.candidate_offset : args.candidate_offset + candidate_count
    ]
    if len(selected_candidates) != candidate_count:
        raise RuntimeError("PARAPHRASE_CANDIDATE_RANGE_EXCEEDS_BENCHMARK")
    requests, selected = build_paraphrase_requests(
        benchmark,
        prompt_template=prompt_path.read_text(encoding="utf-8"),
        selected=selected_candidates,
    )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    request_path = output / "paraphrase_requests.jsonl"
    _write_jsonl(request_path, requests)
    manifest = {
        "schema_version": "trial-opt-paraphrase-selection-v1",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "request_jsonl_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
        "model_id": PARAPHRASE_MODEL_ID,
        "prompt_version": PARAPHRASE_PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        "candidate_offset": args.candidate_offset,
        "candidate_count": candidate_count,
        "target_validated_count": target_count,
        "selected_worlds": [
            {"world_id": item.world_id, "language": item.language} for item in selected
        ],
    }
    (output / "selection_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), "selected": len(selected)}).decode())


def _prepare_validation(args: argparse.Namespace) -> None:
    benchmark = _load_benchmark(args.benchmark)
    selected = _load_selection(args.selection_manifest, args.benchmark)
    selection_manifest = orjson.loads(args.selection_manifest.read_bytes())
    offset = int(selection_manifest.get("candidate_offset", 0))
    expected = paraphrase_candidate_worlds(benchmark)[offset : offset + len(selected)]
    if selected != expected:
        raise RuntimeError("PARAPHRASE_SELECTION_NOT_REPRODUCIBLE")
    paraphrases = parse_paraphrase_responses(_jsonl_rows(args.paraphrase_output), selected)
    prompt = (REPOSITORY_ROOT / "prompts" / "patient_extraction_v1.md").read_text(encoding="utf-8")
    requests = build_extraction_requests(
        paraphrases,
        patient_prompt_template=prompt,
        response_schema=PatientExtractionResult.model_json_schema(),
    )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    paraphrase_path = output / "paraphrases.json"
    paraphrase_path.write_bytes(canonical_json_bytes(paraphrases))
    request_path = output / "extraction_requests.jsonl"
    _write_jsonl(request_path, requests)
    manifest = {
        "schema_version": "trial-opt-paraphrase-validation-input-v1",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "selection_manifest_sha256": hashlib.sha256(
            args.selection_manifest.read_bytes()
        ).hexdigest(),
        "paraphrase_response_sha256": {
            path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in args.paraphrase_output
        },
        "paraphrases_sha256": hashlib.sha256(paraphrase_path.read_bytes()).hexdigest(),
        "extraction_request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
        "model_id": EXTRACTION_MODEL_ID,
        "selected_count": len(selected),
    }
    (output / "validation_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), "selected": len(selected)}).decode())


async def _validate_online_async(args: argparse.Namespace) -> None:
    paraphrases = orjson.loads(args.paraphrases.read_bytes())
    if not isinstance(paraphrases, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in paraphrases.items()
    ):
        raise RuntimeError("PARAPHRASE_CANDIDATE_FILE_INVALID")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    catalog = load_slot_catalog()
    generator = StructuredGenerator(
        client=create_google_cloud_genai_client(
            Settings(
                google_cloud_project=args.project,
                google_cloud_location="global",
                allow_live_model_calls=True,
            )
        ),
        cache=LocalModelResultCache(args.cache),
        pricing=default_pricing_estimator(),
        usage_guard=InMemoryUsageGuard(),
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def extract(
        world_id: str, narrative: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        try:
            async with semaphore:
                proposal, record = await generator.generate_primary_with_lite_fallback(
                    primary_model_id=EXTRACTION_MODEL_ID,
                    lite_model_id=PARAPHRASE_MODEL_ID,
                    task_name="patient_extraction",
                    prompt=render_prompt(
                        "patient_extraction_v1.md",
                        patient_text=narrative,
                        slot_catalog=compact_patient_slot_catalog(catalog),
                    ),
                    prompt_version="1.1.0",
                    output_schema_version="patient-extraction-v1",
                    slot_catalog_version=catalog.version,
                    normalized_input={
                        "patient_text": narrative,
                        "language_hint": "auto",
                        "evaluation_date": args.evaluation_date.isoformat(),
                        "slot_catalog_version": catalog.version,
                        "existing_facts": [],
                        "task": "initial_extraction",
                    },
                    output_model=PatientExtractionResult,
                    primary_thinking_level="MEDIUM",
                    fallback_thinking_level="HIGH",
                    primary_max_output_tokens=2000,
                    fallback_max_output_tokens=2000,
                    session_id=f"paraphrase-validation:{world_id}",
                )
        except (StructuredGenerationUnavailable, CircuitOpenError) as error:
            return None, {
                "id": world_id,
                "error_code": "STRUCTURED_GENERATION_UNAVAILABLE",
                "error_type": type(error).__name__,
                "error_message": str(error)[:500],
            }
        response = {
            "id": world_id,
            "response": {
                "candidates": [
                    {"content": {"parts": [{"text": proposal.model_dump_json()}], "role": "model"}}
                ]
            },
        }
        return response, record.model_dump(mode="json")

    selected_items = sorted(paraphrases.items())
    if args.world_id:
        requested = set(args.world_id)
        unknown = requested - set(paraphrases)
        if unknown:
            raise RuntimeError(f"PARAPHRASE_WORLD_ID_UNKNOWN:{sorted(unknown)}")
        selected_items = [item for item in selected_items if item[0] in requested]
    # A failed paraphrase is a per-record validation outcome, not evidence that
    # every remaining offline record should be skipped. Keep the production
    # circuit behavior unchanged while allowing this bounded batch to finish.
    offline_failure_threshold = max(5, len(selected_items) + 1)
    for model_id in (EXTRACTION_MODEL_ID, PARAPHRASE_MODEL_ID):
        generator.circuit(model_id).failure_threshold = offline_failure_threshold
    pairs = await asyncio.gather(*(extract(*item) for item in selected_items))
    response_path = output / "extraction_responses.jsonl"
    successes = [response for response, _ in pairs if response is not None]
    failures = [record for response, record in pairs if response is None]
    _write_jsonl(response_path, successes)
    records = [record for response, record in pairs if response is not None]
    _write_jsonl(output / "generation_records.jsonl", records)
    _write_jsonl(output / "failures.jsonl", failures)
    manifest = {
        "schema_version": "trial-opt-paraphrase-online-validation-v1",
        "status": "COMPLETE" if not failures else "INCOMPLETE",
        "model_id": EXTRACTION_MODEL_ID,
        "fallback_model_id": PARAPHRASE_MODEL_ID,
        "evaluation_date": args.evaluation_date.isoformat(),
        "requested_count": len(pairs),
        "response_count": len(successes),
        "failure_count": len(failures),
        "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        "fallback_count": sum(bool(record.get("used_fallback")) for record in records),
        "circuit_failure_threshold": offline_failure_threshold,
        "cache_hit_count": sum(bool(record["usage"]["cache_hit"]) for record in records),
        "estimated_cost_usd": round(
            sum(
                float(record["usage"]["estimated_cost_usd"])
                for record in records
                if not record["usage"]["cache_hit"]
            ),
            8,
        ),
    }
    (output / "online_validation_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), **manifest}).decode())


def _validate_online(args: argparse.Namespace) -> None:
    if not args.allow_live_validation or os.environ.get("ALLOW_LIVE_MODEL_CALLS") != "true":
        raise SystemExit(
            "Online validation requires --allow-live-validation and ALLOW_LIVE_MODEL_CALLS=true"
        )
    asyncio.run(_validate_online_async(args))


def _apply(args: argparse.Namespace) -> None:
    benchmark = _load_benchmark(args.benchmark)
    selected = _load_selection(args.selection_manifest, args.benchmark)
    paraphrases = orjson.loads(args.paraphrases.read_bytes())
    if not isinstance(paraphrases, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in paraphrases.items()
    ):
        raise RuntimeError("PARAPHRASE_CANDIDATE_FILE_INVALID")
    extractions = parse_extraction_responses(_jsonl_rows(args.extraction_output), set(paraphrases))
    updated = apply_validated_paraphrases(
        benchmark,
        paraphrases,
        extractions,
        selected,
    )
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing benchmark: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(updated.model_dump(mode="json"))
    args.output.write_bytes(content)
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(content).hexdigest(),
                "paraphrased_worlds": updated.counts["paraphrased_worlds"],
                "acceptance_eligible": updated.acceptance_eligible,
                "blocking_reasons": updated.blocking_reasons,
            }
        ).decode()
    )


def _consolidate_validation(args: argparse.Namespace) -> None:
    benchmark = _load_benchmark(args.benchmark)
    selected = _load_selection(args.selection_manifest, args.benchmark)
    expected = {item.world_id for item in selected}
    paraphrases = orjson.loads(args.paraphrases.read_bytes())
    if (
        not isinstance(paraphrases, dict)
        or set(paraphrases) != expected
        or not all(isinstance(value, str) for value in paraphrases.values())
    ):
        raise RuntimeError("PARAPHRASE_CONSOLIDATION_INPUT_SET_INVALID")
    worlds = {world.world_id: world for world in benchmark.worlds}
    accepted: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for row in _jsonl_rows(args.extraction_output):
        world_id = row.get("id")
        if not isinstance(world_id, str) or world_id not in expected:
            raise RuntimeError("PARAPHRASE_CONSOLIDATION_RESPONSE_ID_INVALID")
        if world_id in accepted:
            continue
        try:
            extraction = parse_extraction_responses([row], {world_id})[world_id]
            validate_paraphrase_spans(worlds[world_id], str(paraphrases[world_id]), extraction)
        except ValueError as error:
            rejected.append(
                {
                    "id": world_id,
                    "error_type": type(error).__name__,
                    "error_code": str(error).split(":", 1)[0][:200],
                }
            )
            continue
        accepted[world_id] = row
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    response_path = output / "extraction_responses.jsonl"
    _write_jsonl(response_path, [accepted[key] for key in sorted(accepted)])
    rejection_path = output / "rejected_extractions.jsonl"
    _write_jsonl(rejection_path, rejected)
    missing = sorted(expected - set(accepted))
    manifest = {
        "schema_version": "trial-opt-paraphrase-consolidation-v1",
        "status": "COMPLETE" if not missing else "INCOMPLETE",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "selection_manifest_sha256": hashlib.sha256(
            args.selection_manifest.read_bytes()
        ).hexdigest(),
        "paraphrases_sha256": hashlib.sha256(args.paraphrases.read_bytes()).hexdigest(),
        "input_sha256": {
            path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in args.extraction_output
        },
        "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        "accepted_count": len(accepted),
        "rejected_attempt_count": len(rejected),
        "missing_world_ids": missing,
    }
    (output / "consolidation_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), **manifest}).decode())


def _finalize_candidate_pool(args: argparse.Namespace) -> None:
    if not (len(args.selection_manifest) == len(args.paraphrases) == len(args.extraction_output)):
        raise RuntimeError("PARAPHRASE_POOL_BUNDLE_COUNT_MISMATCH")
    benchmark = _load_benchmark(args.benchmark)
    candidate_order = paraphrase_candidate_worlds(benchmark)
    target_count = len(select_paraphrase_worlds(benchmark))
    worlds = {world.world_id: world for world in benchmark.worlds}
    validated: dict[str, tuple[SelectedWorld, str, PatientExtractionResult, dict[str, Any]]] = {}
    bundle_hashes = []
    for selection_path, paraphrase_path, extraction_path in zip(
        args.selection_manifest,
        args.paraphrases,
        args.extraction_output,
        strict=True,
    ):
        selected = _load_selection(selection_path, args.benchmark)
        selected_by_id = {item.world_id: item for item in selected}
        candidate_paraphrases = orjson.loads(paraphrase_path.read_bytes())
        if not isinstance(candidate_paraphrases, dict) or set(candidate_paraphrases) != set(
            selected_by_id
        ):
            raise RuntimeError("PARAPHRASE_POOL_CANDIDATE_SET_MISMATCH")
        for row in _jsonl_rows([extraction_path]):
            world_id = row.get("id")
            if not isinstance(world_id, str) or world_id not in selected_by_id:
                raise RuntimeError("PARAPHRASE_POOL_RESPONSE_ID_INVALID")
            if world_id in validated:
                continue
            extraction = parse_extraction_responses([row], {world_id})[world_id]
            narrative = candidate_paraphrases[world_id]
            if not isinstance(narrative, str):
                raise RuntimeError("PARAPHRASE_POOL_NARRATIVE_INVALID")
            try:
                validate_paraphrase_spans(worlds[world_id], narrative, extraction)
            except ValueError:
                continue
            validated[world_id] = (
                selected_by_id[world_id],
                narrative,
                extraction,
                row,
            )
        bundle_hashes.append(
            {
                "selection_manifest": selection_path.as_posix(),
                "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
                "paraphrases": paraphrase_path.as_posix(),
                "paraphrases_sha256": hashlib.sha256(paraphrase_path.read_bytes()).hexdigest(),
                "extraction_output": extraction_path.as_posix(),
                "extraction_sha256": hashlib.sha256(extraction_path.read_bytes()).hexdigest(),
            }
        )
    language_targets = {"ko": (target_count + 1) // 2, "en": target_count // 2}
    selected_final = []
    language_counts = {"ko": 0, "en": 0}
    for candidate in candidate_order:
        if candidate.world_id not in validated:
            continue
        if language_counts[candidate.language] >= language_targets[candidate.language]:
            continue
        selected_final.append(candidate)
        language_counts[candidate.language] += 1
        if len(selected_final) == target_count:
            break
    if len(selected_final) != target_count:
        raise RuntimeError(
            "PARAPHRASE_POOL_VALIDATED_CANDIDATES_INSUFFICIENT:"
            f"available={len(validated)}:selected={len(selected_final)}:"
            f"language_counts={language_counts}:targets={language_targets}"
        )
    final_ids = {item.world_id for item in selected_final}
    final_paraphrases = {world_id: validated[world_id][1] for world_id in sorted(final_ids)}
    final_extractions = {world_id: validated[world_id][2] for world_id in final_ids}
    updated = apply_validated_paraphrases(
        benchmark,
        final_paraphrases,
        final_extractions,
        selected_final,
    )
    if not updated.acceptance_eligible:
        raise RuntimeError("PARAPHRASE_POOL_FINAL_BENCHMARK_NOT_ACCEPTANCE_ELIGIBLE")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    paraphrase_output = output / "paraphrases.json"
    paraphrase_output.write_bytes(canonical_json_bytes(final_paraphrases))
    extraction_output = output / "extraction_responses.jsonl"
    _write_jsonl(
        extraction_output,
        [validated[item.world_id][3] for item in selected_final],
    )
    selection_output = output / "selection_manifest.json"
    selection_payload = {
        "schema_version": "trial-opt-paraphrase-final-selection-v1",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "selection_policy": "fixed-seed candidate order; invalid paraphrases discarded",
        "target_validated_count": target_count,
        "language_counts": language_counts,
        "selected_worlds": [
            {"world_id": item.world_id, "language": item.language} for item in selected_final
        ],
    }
    selection_output.write_bytes(canonical_json_bytes(selection_payload))
    manifest = {
        "schema_version": "trial-opt-paraphrase-pool-finalization-v1",
        "status": "COMPLETE",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "candidate_bundle_sha256": bundle_hashes,
        "validated_candidate_count": len(validated),
        "selected_count": target_count,
        "language_counts": language_counts,
        "selection_sha256": hashlib.sha256(selection_output.read_bytes()).hexdigest(),
        "paraphrases_sha256": hashlib.sha256(paraphrase_output.read_bytes()).hexdigest(),
        "extraction_sha256": hashlib.sha256(extraction_output.read_bytes()).hexdigest(),
    }
    (output / "pool_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), **manifest}).decode())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and validate fixed-seed Dataset A paraphrase batches"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--benchmark", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--candidate-offset", type=int, default=0)
    prepare.add_argument("--candidate-count", type=int)
    prepare.set_defaults(handler=_prepare)

    validate = subparsers.add_parser("prepare-validation")
    validate.add_argument("--benchmark", type=Path, required=True)
    validate.add_argument("--selection-manifest", type=Path, required=True)
    validate.add_argument("--paraphrase-output", type=Path, action="append", required=True)
    validate.add_argument("--output-dir", type=Path, required=True)
    validate.set_defaults(handler=_prepare_validation)

    online = subparsers.add_parser("validate-online")
    online.add_argument("--paraphrases", type=Path, required=True)
    online.add_argument("--project", required=True)
    online.add_argument("--output-dir", type=Path, required=True)
    online.add_argument("--evaluation-date", type=date.fromisoformat, default=date(2026, 8, 11))
    online.add_argument("--concurrency", type=int, choices=range(1, 6), default=1)
    online.add_argument("--world-id", action="append")
    online.add_argument(
        "--cache",
        type=Path,
        default=REPOSITORY_ROOT / ".local_store/paraphrase-validation-model-cache",
    )
    online.add_argument("--allow-live-validation", action="store_true")
    online.set_defaults(handler=_validate_online)

    consolidate = subparsers.add_parser("consolidate-validation")
    consolidate.add_argument("--benchmark", type=Path, required=True)
    consolidate.add_argument("--selection-manifest", type=Path, required=True)
    consolidate.add_argument("--paraphrases", type=Path, required=True)
    consolidate.add_argument("--extraction-output", type=Path, action="append", required=True)
    consolidate.add_argument("--output-dir", type=Path, required=True)
    consolidate.set_defaults(handler=_consolidate_validation)

    finalize_pool = subparsers.add_parser("finalize-candidate-pool")
    finalize_pool.add_argument("--benchmark", type=Path, required=True)
    finalize_pool.add_argument("--selection-manifest", type=Path, action="append", required=True)
    finalize_pool.add_argument("--paraphrases", type=Path, action="append", required=True)
    finalize_pool.add_argument("--extraction-output", type=Path, action="append", required=True)
    finalize_pool.add_argument("--output-dir", type=Path, required=True)
    finalize_pool.set_defaults(handler=_finalize_candidate_pool)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--benchmark", type=Path, required=True)
    apply.add_argument("--selection-manifest", type=Path, required=True)
    apply.add_argument("--paraphrases", type=Path, required=True)
    apply.add_argument("--extraction-output", type=Path, action="append", required=True)
    apply.add_argument("--output", type=Path, required=True)
    apply.set_defaults(handler=_apply)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
