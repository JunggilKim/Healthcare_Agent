from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.infrastructure.snapshot_loader import load_verified_snapshot  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a frozen TRIAL-OPT snapshot")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    manifest = load_verified_snapshot(args.snapshot, require_complete=args.strict)
    print(
        f"snapshot {manifest.snapshot_version}: {len(manifest.files)} files, "
        f"{len(manifest.cases)} cases, complete={manifest.complete}"
    )


if __name__ == "__main__":
    main()
