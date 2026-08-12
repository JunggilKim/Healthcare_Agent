from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes, canonical_sha256  # noqa: E402
from backend.app.domain.trials import (  # noqa: E402
    CompiledTrial,
    ProtocolReviewArtifact,
    RawTrialRecord,
)
from backend.app.evaluation.corpus import build_release_corpus  # noqa: E402
from backend.app.evaluation.world_generator import generate_trial_worlds  # noqa: E402

CASE_IDS = ("S004", "S008", "S001")
ELIGIBLE_COMPILATION_STATUSES = {"FULLY_VERIFIED", "VERIFIED_EXECUTABLE_SUBSET"}


@dataclass(frozen=True, slots=True)
class Candidate:
    case_id: str
    retrieval_order: int
    result: dict[str, object]
    raw: RawTrialRecord
    compiled: CompiledTrial
    review: ProtocolReviewArtifact
    screening: dict[str, object]
    world_count: int
    criterion_label_count: int
    coverage: dict[str, int]


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [orjson.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _screening_approved(row: dict[str, object]) -> bool:
    return all(
        (
            row.get("relevant_to_seed_case") is True,
            row.get("safe_for_protocol_text_adjudication") is True,
            isinstance(row.get("reviewer_alias"), str) and bool(row["reviewer_alias"]),
            isinstance(row.get("reviewed_at"), str) and bool(row["reviewed_at"]),
        )
    )


def _selection_key(candidate: Candidate) -> tuple[int, int, int, str]:
    return (
        -candidate.world_count,
        -candidate.criterion_label_count,
        candidate.retrieval_order,
        candidate.compiled.nct_id,
    )


def _load_candidates(
    *,
    acquisition_root: Path,
    compilation_root: Path,
    evaluation_date: date,
    require_screening_approval: bool,
) -> dict[str, list[Candidate]]:
    acquisition = orjson.loads((acquisition_root / "acquisition.json").read_bytes())
    acquisition_cases = {
        str(item["case_id"]): item for item in acquisition["cases"] if isinstance(item, dict)
    }
    candidates: dict[str, list[Candidate]] = {case_id: [] for case_id in CASE_IDS}
    for case_id in CASE_IDS:
        case_acquisition = acquisition_root / "sessions" / case_id
        case_compilation = compilation_root / "sessions" / case_id
        raw_rows = [
            RawTrialRecord.model_validate(item)
            for item in orjson.loads((case_acquisition / "raw_trials.json").read_bytes())
        ]
        raw_by_id = {item.nct_id: item for item in raw_rows}
        screening_by_id = {
            str(item["nct_id"]): item
            for item in _load_jsonl(case_acquisition / "screening_assignments.jsonl")
        }
        ordered_ids = [str(item) for item in acquisition_cases[case_id]["selected_nct_ids"]]
        retrieval_order = {nct_id: index for index, nct_id in enumerate(ordered_ids)}
        for result_path in sorted((case_compilation / "results").glob("*.json")):
            result = orjson.loads(result_path.read_bytes())
            if result.get("status") not in ELIGIBLE_COMPILATION_STATUSES:
                continue
            nct_id = str(result["nct_id"])
            screening = screening_by_id[nct_id]
            if require_screening_approval and not _screening_approved(screening):
                continue
            compiled = CompiledTrial.model_validate(
                orjson.loads((compilation_root / str(result["compiled_path"])).read_bytes())
            )
            review = ProtocolReviewArtifact.model_validate(
                orjson.loads((compilation_root / str(result["review_path"])).read_bytes())
            )
            raw = raw_by_id[nct_id]
            build_release_corpus([compiled], [raw], [review])
            try:
                worlds, coverage = generate_trial_worlds(compiled, evaluation_date=evaluation_date)
            except ValueError:
                continue
            candidates[case_id].append(
                Candidate(
                    case_id=case_id,
                    retrieval_order=retrieval_order[nct_id],
                    result=result,
                    raw=raw,
                    compiled=compiled,
                    review=review,
                    screening=screening,
                    world_count=len(worlds),
                    criterion_label_count=sum(len(world.criterion_truth) for world in worlds),
                    coverage=coverage,
                )
            )
    return candidates


def select_candidates(
    candidates: dict[str, list[Candidate]], *, trials_per_case: int
) -> dict[str, list[Candidate]]:
    selected: dict[str, list[Candidate]] = {}
    for case_id in CASE_IDS:
        ranked = sorted(candidates[case_id], key=_selection_key)
        if len(ranked) < trials_per_case:
            raise ValueError(
                f"RELEASE_SELECTION_INSUFFICIENT_CANDIDATES:{case_id}:"
                f"available={len(ranked)}:required={trials_per_case}"
            )
        selected[case_id] = ranked[:trials_per_case]
    return selected


def materialize(
    *,
    selected: dict[str, list[Candidate]],
    output_root: Path,
    acquisition_root: Path,
    compilation_root: Path,
    evaluation_date: date,
    screening_required: bool,
) -> dict[str, object]:
    all_compiled: list[CompiledTrial] = []
    all_raw: list[RawTrialRecord] = []
    all_reviews: list[ProtocolReviewArtifact] = []
    case_summaries: list[dict[str, object]] = []
    for case_id in CASE_IDS:
        rows = selected[case_id]
        compiled = [item.compiled for item in rows]
        raw = [item.raw for item in rows]
        reviews = [item.review for item in rows]
        build_release_corpus(compiled, raw, reviews)
        all_compiled.extend(compiled)
        all_raw.extend(raw)
        all_reviews.extend(reviews)
        case_root = output_root / "sessions" / case_id
        _write(
            case_root / "compiled_trials.json", [item.model_dump(mode="json") for item in compiled]
        )
        _write(case_root / "raw_trials.json", [item.model_dump(mode="json") for item in raw])
        _write(case_root / "reviews.json", [item.model_dump(mode="json") for item in reviews])
        (case_root / "screening_assignments.jsonl").write_bytes(
            b"\n".join(canonical_json_bytes(item.screening) for item in rows) + b"\n"
        )
        preview = [
            {
                "nct_id": item.compiled.nct_id,
                "retrieval_order": item.retrieval_order,
                "compilation_status": item.result["status"],
                "screening_approved": _screening_approved(item.screening),
                "world_count": item.world_count,
                "criterion_label_count": item.criterion_label_count,
                "coverage": item.coverage,
            }
            for item in rows
        ]
        _write(case_root / "generation_preview.json", preview)
        case_summaries.append(
            {
                "case_id": case_id,
                "trial_count": len(rows),
                "world_count": sum(item.world_count for item in rows),
                "criterion_label_count": sum(item.criterion_label_count for item in rows),
                "screening_approved_count": sum(
                    _screening_approved(item.screening) for item in rows
                ),
                "selected_nct_ids": [item.compiled.nct_id for item in rows],
            }
        )
    build_release_corpus(all_compiled, all_raw, all_reviews)
    total_trials = len(all_compiled)
    total_worlds = sum(item.world_count for rows in selected.values() for item in rows)
    total_labels = sum(item.criterion_label_count for rows in selected.values() for item in rows)
    if not 24 <= total_trials <= 36:
        raise ValueError(f"RELEASE_SELECTION_TRIAL_COUNT_INVALID:{total_trials}")
    if total_worlds < 300:
        raise ValueError(f"RELEASE_SELECTION_WORLD_COUNT_INSUFFICIENT:{total_worlds}")
    if total_labels < 1500:
        raise ValueError(f"RELEASE_SELECTION_LABEL_COUNT_INSUFFICIENT:{total_labels}")
    artifact_hashes = {
        path.relative_to(output_root).as_posix(): _sha256(path)
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    manifest: dict[str, object] = {
        "schema_version": "trial-opt-selected-release-corpus-v1",
        "status": (
            "APPROVED_PROJECT_SCREENING" if screening_required else "PENDING_PROJECT_SCREENING"
        ),
        "git_sha": _git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
        "evaluation_date": evaluation_date.isoformat(),
        "source_acquisition_path": acquisition_root.resolve()
        .relative_to(REPOSITORY_ROOT)
        .as_posix(),
        "source_compilation_path": compilation_root.resolve()
        .relative_to(REPOSITORY_ROOT)
        .as_posix(),
        "selection_policy": "MAX_WORLD_COUNT_THEN_LABELS_THEN_RETRIEVAL_ORDER",
        "screening_approval_required": screening_required,
        "trial_count": total_trials,
        "world_count": total_worlds,
        "criterion_label_count": total_labels,
        "cases": case_summaries,
        "corpus_binding_hash": canonical_sha256(
            {
                item.nct_id: {
                    "compiled": item.content_hash,
                    "raw": all_raw[index].source_json_sha256,
                    "review": all_reviews[index].content_hash,
                }
                for index, item in enumerate(all_compiled)
            }
        ),
        "artifact_sha256": artifact_hashes,
    }
    _write(output_root / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select and hash-freeze the 36-trial Dataset A release corpus"
    )
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--compilation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials-per-case", type=int, default=12, choices=range(8, 13))
    parser.add_argument("--evaluation-date", type=date.fromisoformat, default=date(2026, 8, 11))
    parser.add_argument("--require-screening-approval", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("OUTPUT_MUST_BE_A_FRESH_DIRECTORY")
    candidates = _load_candidates(
        acquisition_root=args.acquisition,
        compilation_root=args.compilation,
        evaluation_date=args.evaluation_date,
        require_screening_approval=args.require_screening_approval,
    )
    selected = select_candidates(candidates, trials_per_case=args.trials_per_case)
    args.output.mkdir(parents=True)
    try:
        manifest = materialize(
            selected=selected,
            output_root=args.output,
            acquisition_root=args.acquisition,
            compilation_root=args.compilation,
            evaluation_date=args.evaluation_date,
            screening_required=args.require_screening_approval,
        )
    except Exception:
        # The fresh output contains only regenerable, incomplete selection artifacts.
        for path in sorted(args.output.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        args.output.rmdir()
        raise
    print(
        orjson.dumps(
            {
                "output": str(args.output),
                "status": manifest["status"],
                "trial_count": manifest["trial_count"],
                "world_count": manifest["world_count"],
                "criterion_label_count": manifest["criterion_label_count"],
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
