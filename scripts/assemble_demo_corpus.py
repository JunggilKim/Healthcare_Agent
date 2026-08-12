from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from datetime import UTC, datetime
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

CASE_IDS = ("S004", "S008", "S001")
ELIGIBLE_STATUSES = {"FULLY_VERIFIED", "VERIFIED_EXECUTABLE_SUBSET"}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


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


def _load_rows(path: Path, model: type[RawTrialRecord]) -> list[RawTrialRecord]:
    payload = orjson.loads(path.read_bytes())
    if not isinstance(payload, list):
        raise RuntimeError(f"DEMO_CORPUS_LIST_REQUIRED:{path}")
    return [model.model_validate(item) for item in payload]


def _source_spec(value: str) -> tuple[str, Path]:
    nct_id, separator, root = value.partition("=")
    if not separator or not nct_id.startswith("NCT") or not root:
        raise argparse.ArgumentTypeError("source must be NCT########=compilation-root")
    return nct_id, Path(root)


def _compiled_source(
    case_id: str, nct_id: str, root: Path
) -> tuple[CompiledTrial, ProtocolReviewArtifact, dict[str, object]]:
    result_path = root / "sessions" / case_id / "results" / f"{nct_id}.json"
    result = orjson.loads(result_path.read_bytes())
    if result.get("status") not in ELIGIBLE_STATUSES:
        raise RuntimeError(f"DEMO_CORPUS_COMPILATION_NOT_ELIGIBLE:{nct_id}:{result.get('status')}")
    compiled = CompiledTrial.model_validate(
        orjson.loads((root / str(result["compiled_path"])).read_bytes())
    )
    review = ProtocolReviewArtifact.model_validate(
        orjson.loads((root / str(result["review_path"])).read_bytes())
    )
    if compiled.nct_id != nct_id or review.nct_id != nct_id:
        raise RuntimeError(f"DEMO_CORPUS_NCT_BINDING_MISMATCH:{nct_id}")
    return compiled, review, result


def _case_source_spec(value: str) -> tuple[str, str, Path]:
    case_id, separator, source = value.partition(":")
    if not separator or case_id not in CASE_IDS:
        raise argparse.ArgumentTypeError("case source must begin CASE_ID:")
    nct_id, root = _source_spec(source)
    return case_id, nct_id, root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble an exact multi-source 24-trial snapshot corpus"
    )
    parser.add_argument("--base-corpus", type=Path, required=True)
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--s004-source", action="append", type=_source_spec, required=True)
    parser.add_argument(
        "--case-source",
        action="append",
        type=_case_source_spec,
        default=[],
        help="override a non-S004 case source as CASE_ID:NCT########=compilation-root",
    )
    parser.add_argument("--trials-per-case", type=int, choices=range(8, 13), default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("OUTPUT_MUST_BE_A_FRESH_DIRECTORY")
    if len(args.s004_source) != args.trials_per_case:
        raise RuntimeError("DEMO_CORPUS_S004_SOURCE_COUNT_INVALID")
    if len({nct_id for nct_id, _ in args.s004_source}) != len(args.s004_source):
        raise RuntimeError("DEMO_CORPUS_S004_SOURCE_DUPLICATE")
    case_sources: dict[str, list[tuple[str, Path]]] = {}
    for case_id, nct_id, root in args.case_source:
        if case_id == "S004":
            raise RuntimeError("DEMO_CORPUS_S004_USE_DEDICATED_SOURCE_ARGUMENT")
        case_sources.setdefault(case_id, []).append((nct_id, root))
    for case_id, sources in case_sources.items():
        if len(sources) != args.trials_per_case:
            raise RuntimeError(f"DEMO_CORPUS_CASE_SOURCE_COUNT_INVALID:{case_id}")
        if len({nct_id for nct_id, _ in sources}) != len(sources):
            raise RuntimeError(f"DEMO_CORPUS_CASE_SOURCE_DUPLICATE:{case_id}")

    acquisition = orjson.loads((args.acquisition / "acquisition.json").read_bytes())
    case_acquisition = {
        str(item["case_id"]): item for item in acquisition["cases"] if isinstance(item, dict)
    }
    base_manifest = orjson.loads((args.base_corpus / "manifest.json").read_bytes())
    base_case_ids = {
        str(item["case_id"]): [str(nct_id) for nct_id in item["selected_nct_ids"]]
        for item in base_manifest["cases"]
        if isinstance(item, dict)
    }

    all_compiled: list[CompiledTrial] = []
    all_raw: list[RawTrialRecord] = []
    all_reviews: list[ProtocolReviewArtifact] = []
    case_summaries = []
    source_records: dict[str, str] = {}
    for case_id in CASE_IDS:
        raw_pool = {
            item.nct_id: item
            for item in _load_rows(
                args.acquisition / "sessions" / case_id / "raw_trials.json", RawTrialRecord
            )
        }
        screening_rows = {
            str(item["nct_id"]): item
            for item in (
                orjson.loads(line)
                for line in (
                    args.acquisition / "sessions" / case_id / "screening_assignments.jsonl"
                )
                .read_bytes()
                .splitlines()
                if line.strip()
            )
        }
        if case_id == "S004":
            selected_ids = [nct_id for nct_id, _ in args.s004_source]
            sourced = [
                _compiled_source(case_id, nct_id, root)
                for nct_id, root in args.s004_source
            ]
            compiled = [item[0] for item in sourced]
            reviews = [item[1] for item in sourced]
            source_records.update(
                {
                    nct_id: root.resolve().relative_to(REPOSITORY_ROOT).as_posix()
                    for nct_id, root in args.s004_source
                }
            )
        elif case_id in case_sources:
            sources = case_sources[case_id]
            selected_ids = [nct_id for nct_id, _ in sources]
            sourced = [
                _compiled_source(case_id, nct_id, root) for nct_id, root in sources
            ]
            compiled = [item[0] for item in sourced]
            reviews = [item[1] for item in sourced]
            source_records.update(
                {
                    nct_id: root.resolve().relative_to(REPOSITORY_ROOT).as_posix()
                    for nct_id, root in sources
                }
            )
        else:
            selected_ids = base_case_ids[case_id][: args.trials_per_case]
            base_root = args.base_corpus / "sessions" / case_id
            base_compiled = {
                item.nct_id: item
                for item in (
                    CompiledTrial.model_validate(row)
                    for row in orjson.loads((base_root / "compiled_trials.json").read_bytes())
                )
            }
            base_reviews = {
                item.nct_id: item
                for item in (
                    ProtocolReviewArtifact.model_validate(row)
                    for row in orjson.loads((base_root / "reviews.json").read_bytes())
                )
            }
            compiled = [base_compiled[nct_id] for nct_id in selected_ids]
            reviews = [base_reviews[nct_id] for nct_id in selected_ids]
            source_records.update(
                {
                    nct_id: args.base_corpus.resolve().relative_to(REPOSITORY_ROOT).as_posix()
                    for nct_id in selected_ids
                }
            )
        if not set(selected_ids) <= set(raw_pool):
            raise RuntimeError(f"DEMO_CORPUS_RAW_TRIAL_MISSING:{case_id}")
        if not set(selected_ids) <= set(screening_rows):
            raise RuntimeError(f"DEMO_CORPUS_SCREENING_MISSING:{case_id}")
        raw = [raw_pool[nct_id] for nct_id in selected_ids]
        build_release_corpus(compiled, raw, reviews)
        case_root = args.output / "sessions" / case_id
        _write(
            case_root / "compiled_trials.json", [item.model_dump(mode="json") for item in compiled]
        )
        _write(case_root / "raw_trials.json", [item.model_dump(mode="json") for item in raw])
        _write(case_root / "reviews.json", [item.model_dump(mode="json") for item in reviews])
        screening_path = case_root / "screening_assignments.jsonl"
        screening_path.parent.mkdir(parents=True, exist_ok=True)
        screening_path.write_bytes(
            b"\n".join(canonical_json_bytes(screening_rows[nct_id]) for nct_id in selected_ids)
            + b"\n"
        )
        all_compiled.extend(compiled)
        all_raw.extend(raw)
        all_reviews.extend(reviews)
        case_summaries.append(
            {
                "case_id": case_id,
                "trial_count": len(selected_ids),
                "selected_nct_ids": selected_ids,
                "source_retrieval_ids": case_acquisition[case_id]["selected_nct_ids"],
            }
        )
    corpus = build_release_corpus(all_compiled, all_raw, all_reviews)
    if len(corpus.compiled_trials) != args.trials_per_case * len(CASE_IDS):
        raise RuntimeError("DEMO_CORPUS_UNIQUE_TRIAL_COUNT_INVALID")
    hashes = {
        path.relative_to(args.output).as_posix(): _sha256(path)
        for path in sorted(args.output.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": "trial-opt-assembled-demo-corpus-v1",
        "status": "PENDING_EXACT_HASH_SNAPSHOT_REVIEW",
        "git_sha": _git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
        "trial_count": len(corpus.compiled_trials),
        "trials_per_case": args.trials_per_case,
        "source_acquisition_path": args.acquisition.resolve()
        .relative_to(REPOSITORY_ROOT)
        .as_posix(),
        "source_acquisition_sha256": _sha256(args.acquisition / "acquisition.json"),
        "source_records": source_records,
        "cases": case_summaries,
        "corpus_binding_hash": canonical_sha256(
            {
                nct_id: {
                    "compiled": corpus.compiled_trials[nct_id].content_hash,
                    "raw": corpus.raw_trials[nct_id].source_json_sha256,
                    "review": corpus.reviews[nct_id].content_hash,
                }
                for nct_id in sorted(corpus.compiled_trials)
            }
        ),
        "artifact_sha256": hashes,
    }
    _write(args.output / "manifest.json", manifest)
    print(orjson.dumps({"output": str(args.output), **manifest}).decode())


if __name__ == "__main__":
    main()
