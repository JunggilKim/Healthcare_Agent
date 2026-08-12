from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.agents.prompts import prompt_sha256  # noqa: E402
from backend.app.application.catalog import load_slot_catalog  # noqa: E402
from backend.app.application.compilation_service import (  # noqa: E402
    ProtocolCompilationService,
)
from backend.app.domain.canonical import canonical_json_bytes, canonical_sha256  # noqa: E402
from backend.app.domain.trials import RawTrialRecord  # noqa: E402
from backend.app.infrastructure.cache import LocalModelResultCache  # noqa: E402
from backend.app.infrastructure.genai_client import (  # noqa: E402
    create_google_cloud_genai_client,
)
from backend.app.infrastructure.structured_generation import StructuredGenerator  # noqa: E402
from backend.app.infrastructure.usage_guard import (  # noqa: E402
    InMemoryUsageGuard,
    default_pricing_estimator,
)
from backend.app.settings import Settings  # noqa: E402

SCHEMA_VERSION = "trial-opt-release-compilation-v1"
CASE_IDS = ("S004", "S008", "S001")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(canonical_json_bytes(payload))
    temporary.replace(path)


def _load_trials(input_root: Path, case_ids: tuple[str, ...]) -> list[tuple[str, RawTrialRecord]]:
    acquisition_path = input_root / "acquisition.json"
    acquisition = orjson.loads(acquisition_path.read_bytes())
    if acquisition.get("schema_version") != "trial-opt-live-corpus-acquisition-v1":
        raise ValueError("RELEASE_COMPILATION_ACQUISITION_SCHEMA_INVALID")
    expected_hashes = acquisition.get("artifact_sha256", {})
    work: list[tuple[str, RawTrialRecord]] = []
    seen: set[str] = set()
    for case_id in case_ids:
        relative = f"sessions/{case_id}/raw_trials.json"
        raw_path = input_root / relative
        if expected_hashes.get(relative) != _sha256(raw_path):
            raise ValueError(f"RELEASE_COMPILATION_RAW_HASH_MISMATCH:{case_id}")
        rows = orjson.loads(raw_path.read_bytes())
        if not isinstance(rows, list):
            raise ValueError(f"RELEASE_COMPILATION_RAW_SHAPE_INVALID:{case_id}")
        for row in rows:
            trial = RawTrialRecord.model_validate(row)
            if trial.nct_id in seen:
                raise ValueError(f"RELEASE_COMPILATION_DUPLICATE_TRIAL:{trial.nct_id}")
            if not trial.eligibility_criteria:
                raise ValueError(f"RELEASE_COMPILATION_ELIGIBILITY_MISSING:{trial.nct_id}")
            seen.add(trial.nct_id)
            work.append((case_id, trial))
    return work


def _binding(
    trial: RawTrialRecord,
    *,
    evaluation_date: date,
    models_config_sha256: str,
    slots_config_sha256: str,
    implementation_sha256: str,
) -> dict[str, object]:
    eligibility = trial.eligibility_criteria or ""
    return {
        "nct_id": trial.nct_id,
        "source_json_sha256": trial.source_json_sha256,
        "eligibility_text_sha256": hashlib.sha256(eligibility.encode()).hexdigest(),
        "evaluation_date": evaluation_date.isoformat(),
        "compiler_prompt_sha256": prompt_sha256("protocol_compiler_v1.md"),
        "reviewer_prompt_sha256": prompt_sha256("protocol_reviewer_v1.md"),
        "models_config_sha256": models_config_sha256,
        "slots_config_sha256": slots_config_sha256,
        "implementation_sha256": implementation_sha256,
        "ast_schema_version": "criterion-ast-v1",
    }


def _is_resumable(result_path: Path, binding: dict[str, object], output_root: Path) -> bool:
    if not result_path.is_file():
        return False
    try:
        result = orjson.loads(result_path.read_bytes())
        if result.get("schema_version") != SCHEMA_VERSION or result.get("binding") != binding:
            return False
        for relative, expected in result.get("artifact_sha256", {}).items():
            path = output_root / relative
            if not path.is_file() or _sha256(path) != expected:
                return False
    except (OSError, ValueError, TypeError, orjson.JSONDecodeError):
        return False
    return True


async def _compile_one(
    *,
    case_id: str,
    trial: RawTrialRecord,
    service: ProtocolCompilationService,
    usage_guard: InMemoryUsageGuard,
    evaluation_date: date,
    binding: dict[str, object],
    output_root: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, object]:
    case_root = output_root / "sessions" / case_id
    artifact_root = case_root / "artifacts" / trial.nct_id
    result_path = case_root / "results" / f"{trial.nct_id}.json"
    if _is_resumable(result_path, binding, output_root):
        result = orjson.loads(result_path.read_bytes())
        print(
            orjson.dumps(
                {"nct_id": trial.nct_id, "status": result["status"], "resume": True}
            ).decode(),
            flush=True,
        )
        return result

    session_id = f"release-compilation:{case_id}:{trial.nct_id}"
    started_at = datetime.now(UTC)
    artifact_hashes: dict[str, str] = {}
    try:
        async with semaphore:
            workflow = await service.compile_and_review(
                trial=trial,
                evaluation_date=evaluation_date,
                now=started_at,
                session_id=session_id,
            )
        compiled = workflow.compilation.compiled_trial
        compiled_path = artifact_root / "compiled.json"
        _atomic_write(compiled_path, compiled.model_dump(mode="json"))
        compiled_relative = compiled_path.relative_to(output_root).as_posix()
        artifact_hashes[compiled_relative] = _sha256(compiled_path)

        coverage_path = artifact_root / "coverage.json"
        coverage_payload = {
            "coverage": asdict(workflow.compilation.coverage_report),
            "boundary_reports": {
                criterion_id: asdict(report)
                for criterion_id, report in workflow.compilation.boundary_reports.items()
            },
        }
        _atomic_write(coverage_path, coverage_payload)
        coverage_relative = coverage_path.relative_to(output_root).as_posix()
        artifact_hashes[coverage_relative] = _sha256(coverage_path)

        review_relative: str | None = None
        if workflow.review_artifact is not None:
            review_path = artifact_root / "review.json"
            _atomic_write(review_path, workflow.review_artifact.model_dump(mode="json"))
            review_relative = review_path.relative_to(output_root).as_posix()
            artifact_hashes[review_relative] = _sha256(review_path)

        approved_review = workflow.review_artifact is not None and workflow.review_artifact.approved
        verified_executable_count = sum(
            criterion.protocol_verified and not criterion.opaque for criterion in compiled.criteria
        )
        if (
            compiled.protocol_verified
            and approved_review
            and compiled.boundary_tests_passed
            and compiled.source_character_coverage >= 0.90
        ):
            status = "FULLY_VERIFIED"
        elif (
            approved_review
            and verified_executable_count > 0
            and compiled.boundary_tests_passed
            and compiled.source_character_coverage >= 0.90
        ):
            status = "VERIFIED_EXECUTABLE_SUBSET"
        else:
            status = "REVIEW_REQUIRED"
        usage = usage_guard.snapshot(session_id)
        result: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "nct_id": trial.nct_id,
            "status": status,
            "binding": binding,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "compiled_path": compiled_relative,
            "review_path": review_relative,
            "coverage_path": coverage_relative,
            "compiled_content_hash": compiled.content_hash,
            "review_content_hash": (
                workflow.review_artifact.content_hash if workflow.review_artifact else None
            ),
            "protocol_verified": compiled.protocol_verified,
            "source_character_coverage": compiled.source_character_coverage,
            "boundary_tests_passed": compiled.boundary_tests_passed,
            "criteria_count": len(compiled.criteria),
            "opaque_criteria_count": sum(item.opaque for item in compiled.criteria),
            "verified_executable_criteria_count": verified_executable_count,
            "repair_attempted": workflow.repair_attempted,
            "degradation_codes": workflow.degradation_codes,
            "estimated_cost_usd": float(usage.session_reconciled_usd),
            "artifact_sha256": artifact_hashes,
        }
    except Exception as error:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "nct_id": trial.nct_id,
            "status": "ERROR",
            "binding": binding,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
            "artifact_sha256": artifact_hashes,
        }
    _atomic_write(result_path, result)
    print(
        orjson.dumps(
            {"nct_id": trial.nct_id, "status": result["status"], "resume": False}
        ).decode(),
        flush=True,
    )
    return result


def _materialize_aggregates(
    *,
    results: list[dict[str, object]],
    output_root: Path,
    input_root: Path,
    project: str,
) -> dict[str, object]:
    fully_verified_count = 0
    executable_subset_count = 0
    review_required_count = 0
    error_count = 0
    case_summaries: list[dict[str, object]] = []
    for case_id in CASE_IDS:
        case_results = sorted(
            (item for item in results if item["case_id"] == case_id),
            key=lambda item: str(item["nct_id"]),
        )
        verified_trials: list[object] = []
        reviews: list[object] = []
        provisional_trials: list[object] = []
        for item in case_results:
            status = item["status"]
            if status == "ERROR":
                error_count += 1
                continue
            compiled_path = output_root / str(item["compiled_path"])
            compiled = orjson.loads(compiled_path.read_bytes())
            if status in {"FULLY_VERIFIED", "VERIFIED_EXECUTABLE_SUBSET"}:
                if status == "FULLY_VERIFIED":
                    fully_verified_count += 1
                else:
                    executable_subset_count += 1
                verified_trials.append(compiled)
                review_path = output_root / str(item["review_path"])
                reviews.append(orjson.loads(review_path.read_bytes()))
            else:
                review_required_count += 1
                provisional_trials.append(compiled)
        case_root = output_root / "sessions" / case_id
        _atomic_write(case_root / "compiled_trials.json", verified_trials)
        _atomic_write(case_root / "reviews.json", reviews)
        _atomic_write(case_root / "provisional_trials.json", provisional_trials)
        case_summaries.append(
            {
                "case_id": case_id,
                "trial_count": len(case_results),
                "fully_verified_count": sum(
                    item["status"] == "FULLY_VERIFIED" for item in case_results
                ),
                "executable_subset_count": sum(
                    item["status"] == "VERIFIED_EXECUTABLE_SUBSET" for item in case_results
                ),
                "dataset_a_candidate_count": len(verified_trials),
                "review_required_count": len(provisional_trials),
                "error_count": sum(item["status"] == "ERROR" for item in case_results),
            }
        )

    artifact_hashes: dict[str, str] = {}
    for path in sorted(output_root.rglob("*.json")):
        if path == output_root / "manifest.json":
            continue
        relative = path.relative_to(output_root).as_posix()
        artifact_hashes[relative] = _sha256(path)
    manifest = {
        "schema_version": "trial-opt-release-compilation-manifest-v1",
        "status": "PENDING_PROJECT_SCREENING",
        "project_id": project,
        "git_sha": _git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
        "source_acquisition_path": input_root.relative_to(REPOSITORY_ROOT).as_posix(),
        "source_acquisition_sha256": _sha256(input_root / "acquisition.json"),
        "case_ids": list(CASE_IDS),
        "trial_count": len(results),
        "fully_verified_count": fully_verified_count,
        "executable_subset_count": executable_subset_count,
        "dataset_a_candidate_count": fully_verified_count + executable_subset_count,
        "review_required_count": review_required_count,
        "error_count": error_count,
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0.0)) for item in results), 8
        ),
        "cases": case_summaries,
        "result_hash": canonical_sha256(
            [
                {
                    "case_id": item["case_id"],
                    "nct_id": item["nct_id"],
                    "status": item["status"],
                    "binding": item["binding"],
                    "compiled_content_hash": item.get("compiled_content_hash"),
                    "review_content_hash": item.get("review_content_hash"),
                }
                for item in sorted(results, key=lambda value: str(value["nct_id"]))
            ]
        ),
        "artifact_sha256": artifact_hashes,
    }
    _atomic_write(output_root / "manifest.json", manifest)
    return manifest


async def compile_corpus(args: argparse.Namespace) -> dict[str, object]:
    selected_cases = tuple(args.case) if args.case else CASE_IDS
    if any(case_id not in CASE_IDS for case_id in selected_cases):
        raise ValueError("RELEASE_COMPILATION_CASE_INVALID")
    work = _load_trials(args.input, selected_cases)
    if args.limit is not None:
        work = work[: args.limit]
    if not work:
        raise ValueError("RELEASE_COMPILATION_WORKSET_EMPTY")

    settings = Settings(
        google_cloud_project=args.project,
        google_cloud_location="global",
        allow_live_model_calls=True,
    )
    usage_guard = InMemoryUsageGuard()
    generator = StructuredGenerator(
        client=create_google_cloud_genai_client(settings),
        cache=LocalModelResultCache(args.cache),
        pricing=default_pricing_estimator(),
        usage_guard=usage_guard,
    )
    service = ProtocolCompilationService(generator, load_slot_catalog())
    models_hash = _sha256(REPOSITORY_ROOT / "config" / "models.yaml")
    slots_hash = _sha256(REPOSITORY_ROOT / "config" / "slots.yaml")
    implementation_hash = hashlib.sha256(
        b"".join(
            path.read_bytes()
            for path in (
                REPOSITORY_ROOT / "backend" / "app" / "agents" / "protocol_compiler.py",
                REPOSITORY_ROOT / "backend" / "app" / "application" / "compilation_service.py",
                REPOSITORY_ROOT / "backend" / "app" / "infrastructure" / "structured_generation.py",
            )
        )
    ).hexdigest()
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        _compile_one(
            case_id=case_id,
            trial=trial,
            service=service,
            usage_guard=usage_guard,
            evaluation_date=args.evaluation_date,
            binding=_binding(
                trial,
                evaluation_date=args.evaluation_date,
                models_config_sha256=models_hash,
                slots_config_sha256=slots_hash,
                implementation_sha256=implementation_hash,
            ),
            output_root=args.output,
            semaphore=semaphore,
        )
        for case_id, trial in work
    ]
    results = await asyncio.gather(*tasks)
    return _materialize_aggregates(
        results=results,
        output_root=args.output,
        input_root=args.input,
        project=args.project,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resumably compile and semantically review the hash-bound release corpus"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--input", type=Path, default=REPOSITORY_ROOT / "data" / "demo" / "acquisition-20260812"
    )
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "data" / "demo" / "compilation-20260812"
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=REPOSITORY_ROOT / ".local_store" / "release-compile-model-cache",
    )
    parser.add_argument("--evaluation-date", type=date.fromisoformat, default=date(2026, 8, 12))
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-live-compilation", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_compilation:
        raise SystemExit("Refusing paid compilation without --allow-live-compilation")
    if os.environ.get("ALLOW_LIVE_MODEL_CALLS", "").lower() != "true":
        raise SystemExit("Refusing paid compilation unless ALLOW_LIVE_MODEL_CALLS=true")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    manifest = asyncio.run(compile_corpus(args))
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "trial_count": manifest["trial_count"],
                "fully_verified_count": manifest["fully_verified_count"],
                "executable_subset_count": manifest["executable_subset_count"],
                "dataset_a_candidate_count": manifest["dataset_a_candidate_count"],
                "review_required_count": manifest["review_required_count"],
                "error_count": manifest["error_count"],
                "estimated_cost_usd": manifest["estimated_cost_usd"],
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
