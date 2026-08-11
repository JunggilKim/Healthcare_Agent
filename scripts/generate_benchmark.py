from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.application.vertical_slice import load_vertical_slice  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.worlds import generate_fixture_benchmark  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic structured patient worlds")
    parser.add_argument("--config", type=Path, default=Path("config/eval.yaml"))
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--output", type=Path, default=Path("data/eval/generated/benchmark.json"))
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    if args.seed != int(config["seed"]):
        raise RuntimeError("SEED_MUST_MATCH_COMMITTED_EVAL_CONFIG")
    artifact = generate_fixture_benchmark(load_vertical_slice(), args.seed)
    content = canonical_json_bytes(artifact.model_dump(mode="json"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    summary = {
        "output": str(args.output),
        "sha256": hashlib.sha256(content).hexdigest(),
        "scope_status": artifact.scope_status,
        "acceptance_eligible": artifact.acceptance_eligible,
        "counts": artifact.counts,
    }
    print(orjson.dumps(summary).decode())


if __name__ == "__main__":
    main()
