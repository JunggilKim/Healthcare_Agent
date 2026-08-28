from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.quality_gate import evaluate_engineering_quality_gate  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check deterministic accuracy regressions for the current commit"
    )
    parser.add_argument("--suite-root", type=Path, default=Path("artifacts/eval/latest/suites"))
    return parser.parse_args()


def main() -> None:
    args = _args()
    suite_paths = {
        name: args.suite_root / f"{name}.json"
        for name in (
            "retrieval",
            "criterion",
            "interactive",
            "ablation",
        )
    }
    suites: dict[str, dict[str, Any]] = {
        name: orjson.loads(path.read_bytes())
        for name, path in suite_paths.items()
        if path.is_file()
    }
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = evaluate_engineering_quality_gate(suites, expected_git_sha=git_sha)
    print(canonical_json_bytes(result).decode())
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
