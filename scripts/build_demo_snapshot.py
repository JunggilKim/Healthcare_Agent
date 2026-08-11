from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.infrastructure.snapshot_loader import (  # noqa: E402
    SnapshotCase,
    SnapshotFile,
    SnapshotManifest,
)

REQUIRED_CASE_ARTIFACTS = (
    "initial.json",
    "raw_trials.json",
    "retrieval.json",
    "embeddings.json",
    "embeddings.npz",
    "compiled_trials.json",
    "proofs.json",
    "ranking.json",
    "questions.json",
    "reports.json",
    "experiment_summary.json",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze reviewed TRIAL-OPT demo artifacts by hash")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--mode", choices=["live", "prepared"], required=True)
    parser.add_argument("--manual-review-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("data/demo/prepared"))
    parser.add_argument("--snapshot-version", default="")
    parser.add_argument("--data-timestamp", default="")
    return parser.parse_args()


def _validate_source(case_root: Path) -> list[Path]:
    missing = [name for name in REQUIRED_CASE_ARTIFACTS if not (case_root / name).is_file()]
    branches = (
        list((case_root / "branches").rglob("*.json")) if (case_root / "branches").is_dir() else []
    )
    if not branches:
        missing.append("branches/**/*.json")
    if missing:
        raise RuntimeError(f"SNAPSHOT_CASE_INCOMPLETE:{case_root.name}:" + ",".join(missing))
    initial = orjson.loads((case_root / "initial.json").read_bytes())
    questions = orjson.loads((case_root / "questions.json").read_bytes())
    selected = (initial.get("current_question") or {}).get("selected") or {}
    expected_first = {str(item["branch_id"]) for item in selected.get("branches", [])}
    if not expected_first:
        raise RuntimeError(f"SNAPSHOT_FIRST_QUESTION_BRANCHES_MISSING:{case_root.name}")
    recorded = {
        str(item.get("branch_id"))
        for item in questions.get("branches", [])
        if int(item.get("depth", 1)) == 1
    }
    if expected_first - recorded:
        raise RuntimeError(f"SNAPSHOT_FIRST_BRANCHES_INCOMPLETE:{case_root.name}")
    if case_root.name == "S004" and not any(
        int(item.get("depth", 1)) >= 2 for item in questions.get("branches", [])
    ):
        raise RuntimeError("SNAPSHOT_S004_SEQUENTIAL_BRANCH_MISSING")
    return [*(case_root / name for name in REQUIRED_CASE_ARTIFACTS), *sorted(branches)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nct_ids(value: object) -> set[str]:
    if isinstance(value, dict):
        found = {
            item
            for key, item in value.items()
            if key == "nct_id" and isinstance(item, str) and re.fullmatch(r"NCT\d{8}", item)
        }
        return found | {nct_id for item in value.values() for nct_id in _nct_ids(item)}
    if isinstance(value, list):
        return {nct_id for item in value for nct_id in _nct_ids(item)}
    return set()


def _validate_corpus_size(source: Path, cases: list[str]) -> None:
    all_ids: set[str] = set()
    for case_id in cases:
        case_root = source / "sessions" / case_id
        compiled_ids = _nct_ids(orjson.loads((case_root / "compiled_trials.json").read_bytes()))
        raw_ids = _nct_ids(orjson.loads((case_root / "raw_trials.json").read_bytes()))
        if not 8 <= len(compiled_ids) <= 12:
            raise RuntimeError(f"SNAPSHOT_CASE_TRIAL_COUNT_INVALID:{case_id}:{len(compiled_ids)}")
        if not compiled_ids <= raw_ids:
            raise RuntimeError(f"SNAPSHOT_RAW_TRIAL_SET_INCOMPLETE:{case_id}")
        all_ids.update(compiled_ids)
    if not 24 <= len(all_ids) <= 36:
        raise RuntimeError(f"SNAPSHOT_UNIQUE_TRIAL_COUNT_INVALID:{len(all_ids)}")


def _validate_live_provenance(
    *,
    source: Path,
    cases: list[str],
    source_files: dict[str, list[Path]],
    review_path: Path,
) -> tuple[dict[str, object], str]:
    acquisition_path = source / "acquisition.json"
    if not acquisition_path.is_file():
        raise RuntimeError("LIVE_ACQUISITION_MANIFEST_MISSING")
    acquisition = orjson.loads(acquisition_path.read_bytes())
    if not isinstance(acquisition, dict):
        raise RuntimeError("LIVE_ACQUISITION_MANIFEST_INVALID")
    if (
        acquisition.get("schema_version") != "trial-opt-live-acquisition-v1"
        or acquisition.get("mode") != "LIVE"
        or acquisition.get("case_ids") != cases
    ):
        raise RuntimeError("LIVE_ACQUISITION_MANIFEST_INVALID")
    declared = acquisition.get("artifact_sha256")
    if not isinstance(declared, dict):
        raise RuntimeError("LIVE_ACQUISITION_HASHES_MISSING")
    for paths in source_files.values():
        for path in paths:
            relative = path.relative_to(source).as_posix()
            if declared.get(relative) != _sha256(path):
                raise RuntimeError(f"LIVE_ACQUISITION_HASH_MISMATCH:{relative}")
    data_timestamp = str(acquisition.get("data_timestamp", ""))
    if not data_timestamp:
        raise RuntimeError("LIVE_ACQUISITION_DATA_TIMESTAMP_MISSING")

    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    if not isinstance(review, dict) or review.get("status") != "APPROVED":
        raise RuntimeError("MANUAL_REVIEW_NOT_APPROVED")
    reviews = review.get("cases")
    if not isinstance(reviews, list):
        raise RuntimeError("MANUAL_REVIEW_CASES_MISSING")
    by_case = {str(item.get("case_id")): item for item in reviews if isinstance(item, dict)}
    for case_id in cases:
        item = by_case.get(case_id, {})
        case_root = source / "sessions" / case_id
        if (
            item.get("approved") is not True
            or not item.get("reviewer_alias")
            or not item.get("reviewed_at")
            or item.get("compiled_trials_sha256") != _sha256(case_root / "compiled_trials.json")
            or item.get("proofs_sha256") != _sha256(case_root / "proofs.json")
        ):
            raise RuntimeError(f"MANUAL_REVIEW_HASH_BINDING_INVALID:{case_id}")
    return acquisition, data_timestamp


def main() -> None:
    args = _arguments()
    cases = [item.strip() for item in args.cases.split(",") if item.strip()]
    if cases != ["S004", "S008", "S001"]:
        raise RuntimeError("SNAPSHOT_CASE_SET_MUST_BE_S004_S008_S001")
    if not args.manual_review_manifest.is_file():
        raise RuntimeError("MANUAL_REVIEW_MANIFEST_MISSING")
    source_files = {
        case_id: _validate_source(args.source / "sessions" / case_id) for case_id in cases
    }
    acquisition: dict[str, object] | None = None
    data_timestamp = args.data_timestamp
    if args.mode == "live":
        _validate_corpus_size(args.source, cases)
        acquisition, data_timestamp = _validate_live_provenance(
            source=args.source,
            cases=cases,
            source_files=source_files,
            review_path=args.manual_review_manifest,
        )
    elif not data_timestamp:
        raise RuntimeError("DATA_TIMESTAMP_REQUIRED")
    if args.output.exists():
        raise RuntimeError("OUTPUT_MUST_BE_A_FRESH_DIRECTORY")
    args.output.mkdir(parents=True)
    entries: list[SnapshotFile] = []
    case_entries: list[SnapshotCase] = []
    for case_id, paths in source_files.items():
        artifact_paths: list[str] = []
        for source_path in paths:
            relative_under_case = source_path.relative_to(args.source / "sessions" / case_id)
            relative = Path("sessions") / case_id / relative_under_case
            target = args.output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
            content = target.read_bytes()
            relative_text = relative.as_posix()
            artifact_paths.append(relative_text)
            entries.append(
                SnapshotFile(
                    path=relative_text,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
        case_entries.append(
            SnapshotCase(case_id=case_id, complete=True, artifact_paths=artifact_paths)
        )
    review_content = args.manual_review_manifest.read_bytes()
    review_target = args.output / "manual_review.yaml"
    review_target.write_bytes(review_content)
    entries.append(
        SnapshotFile(
            path="manual_review.yaml",
            sha256=hashlib.sha256(review_content).hexdigest(),
            size_bytes=len(review_content),
        )
    )
    if acquisition is not None:
        acquisition_content = canonical_json_bytes(acquisition)
        acquisition_target = args.output / "acquisition.json"
        acquisition_target.write_bytes(acquisition_content)
        entries.append(
            SnapshotFile(
                path="acquisition.json",
                sha256=hashlib.sha256(acquisition_content).hexdigest(),
                size_bytes=len(acquisition_content),
            )
        )
    now = datetime.now(UTC)
    version = args.snapshot_version or now.strftime("%Y%m%dT%H%M%SZ")
    manifest = SnapshotManifest(
        schema_version="trial-opt-snapshot-v1",
        snapshot_version=version,
        built_at=now.isoformat(),
        data_timestamp=data_timestamp,
        cases=case_entries,
        files=sorted(entries, key=lambda item: item.path),
        complete=True,
    )
    (args.output / "manifest.json").write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    print(orjson.dumps({"snapshot_version": version, "files": len(entries)}).decode())


if __name__ == "__main__":
    main()
