from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

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
    parse_extraction_responses,
    parse_paraphrase_responses,
    select_paraphrase_worlds,
)


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
    requests, selected = build_paraphrase_requests(
        benchmark, prompt_template=prompt_path.read_text(encoding="utf-8")
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
        "selected_worlds": [
            {"world_id": item.world_id, "language": item.language} for item in selected
        ],
    }
    (output / "selection_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), "selected": len(selected)}).decode())


def _prepare_validation(args: argparse.Namespace) -> None:
    benchmark = _load_benchmark(args.benchmark)
    selected = _load_selection(args.selection_manifest, args.benchmark)
    if selected != select_paraphrase_worlds(benchmark):
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and validate fixed-seed Dataset A paraphrase batches"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--benchmark", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)

    validate = subparsers.add_parser("prepare-validation")
    validate.add_argument("--benchmark", type=Path, required=True)
    validate.add_argument("--selection-manifest", type=Path, required=True)
    validate.add_argument("--paraphrase-output", type=Path, action="append", required=True)
    validate.add_argument("--output-dir", type=Path, required=True)
    validate.set_defaults(handler=_prepare_validation)

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
