from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import orjson

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
    "retrieval.json",
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
    return [*(case_root / name for name in REQUIRED_CASE_ARTIFACTS), *sorted(branches)]


def main() -> None:
    args = _arguments()
    cases = [item.strip() for item in args.cases.split(",") if item.strip()]
    if cases != ["S004", "S008", "S001"]:
        raise RuntimeError("SNAPSHOT_CASE_SET_MUST_BE_S004_S008_S001")
    if not args.manual_review_manifest.is_file():
        raise RuntimeError("MANUAL_REVIEW_MANIFEST_MISSING")
    if args.mode == "live":
        raise RuntimeError(
            "LIVE_SNAPSHOT_BUILD_REQUIRES_EXPLICIT_GCP_ORCHESTRATOR; "
            "prepare reviewed artifacts first"
        )
    if not args.data_timestamp:
        raise RuntimeError("DATA_TIMESTAMP_REQUIRED")

    source_files = {
        case_id: _validate_source(args.source / "sessions" / case_id) for case_id in cases
    }
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
    now = datetime.now(UTC)
    version = args.snapshot_version or now.strftime("%Y%m%dT%H%M%SZ")
    manifest = SnapshotManifest(
        schema_version="trial-opt-snapshot-v1",
        snapshot_version=version,
        built_at=now.isoformat(),
        data_timestamp=args.data_timestamp,
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
