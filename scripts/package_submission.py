from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import orjson

ROOT = Path(__file__).resolve().parents[1]


def copy_required(source: Path, target: Path, *, final: bool, missing: list[str]) -> None:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    elif final:
        raise SystemExit(f"Required final artifact is missing: {source.relative_to(ROOT)}")
    else:
        missing.append(str(source.relative_to(ROOT)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a guarded TRIAL-OPT submission bundle")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--final", action="store_true")
    mode.add_argument("--provisional", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("artifacts/submission"))
    args = parser.parse_args()
    output = (ROOT / args.output).resolve() if not args.output.is_absolute() else args.output
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")

    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    verification_path = ROOT / "artifacts/release/verification.json"
    verification: dict[str, object] = {}
    if verification_path.is_file():
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if args.final:
        if verification.get("passed") is not True or verification.get("git_sha") != git_sha:
            raise SystemExit("Final packaging requires a passing strict verifier bound to HEAD")
        tags = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        if "v1.0.0-challenge" not in tags:
            raise SystemExit("Final packaging requires v1.0.0-challenge at HEAD")

    output.mkdir(parents=True)
    missing: list[str] = []
    (output / "SOURCE_COMMIT.txt").write_text(git_sha + "\n", encoding="utf-8")
    mappings = {
        "artifacts/release/IMAGE_DIGEST.txt": "IMAGE_DIGEST.txt",
        "data/demo/current/manifest.json": "SNAPSHOT_MANIFEST.json",
        "artifacts/release/verification.md": "RELEASE_VERIFICATION.md",
        "artifacts/release/verification.json": "RELEASE_VERIFICATION.json",
        "artifacts/eval/latest/summary.md": "EVALUATION_SUMMARY.md",
        "artifacts/eval/latest/metrics.json": "EVALUATION_METRICS.json",
        "DATA_SOURCES.md": "DATA_SOURCES.md",
        "MODEL_AND_COST_CARD.md": "MODEL_AND_COST_CARD.md",
        "SAFETY_AND_LIMITATIONS.md": "SAFETY_AND_LIMITATIONS.md",
        "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
        "README.md": "README.md",
        "uv.lock": "dependency-locks/uv.lock",
        "package-lock.json": "dependency-locks/package-lock.json",
        "docs/DEMO_RUNBOOK.md": "demo-runbook.md",
        "presentation/demo_script.md": "demo-script.md",
        "presentation/submission_checklist.md": "submission-checklist.md",
    }
    for source, target in mappings.items():
        copy_required(ROOT / source, output / target, final=args.final, missing=missing)
    chart_dir = ROOT / "artifacts/eval/latest/charts"
    if chart_dir.is_dir():
        shutil.copytree(chart_dir, output / "presentation-figures")
    elif args.final:
        raise SystemExit("Final evaluation figures are missing")
    else:
        missing.append("artifacts/eval/latest/charts")

    archive = output / "source-archive.tar.gz"
    archive_ref = "v1.0.0-challenge" if args.final else git_sha
    with archive.open("wb") as handle:
        result = subprocess.run(
            ["git", "archive", "--format=tar.gz", archive_ref],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode("utf-8", errors="replace"))

    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            content = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            )
    manifest = {
        "schema_version": "trial-opt-submission-package-v1",
        "status": "FINAL" if args.final else "PROVISIONAL_NOT_SUBMITTABLE",
        "git_sha": git_sha,
        "created_at": datetime.now(UTC).isoformat(),
        "missing_required_final_artifacts": missing,
        "files": files,
    }
    (output / "PACKAGE_MANIFEST.json").write_bytes(
        orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    print(f"Created {manifest['status']} package at {output}; missing={len(missing)}")


if __name__ == "__main__":
    main()
