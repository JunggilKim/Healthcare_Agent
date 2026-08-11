from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.application.vertical_slice import load_vertical_slice  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.domain.trials import RawTrialRecord  # noqa: E402
from backend.app.evaluation.annotations import load_compiled_trials  # noqa: E402
from backend.app.evaluation.world_generator import generate_dataset_a_benchmark  # noqa: E402
from backend.app.evaluation.worlds import generate_fixture_benchmark  # noqa: E402


def _load_raw_trials(paths: list[Path]) -> list[RawTrialRecord]:
    rows: list[object] = []
    for path in paths:
        payload = orjson.loads(path.read_bytes())
        if isinstance(payload, list):
            rows.extend(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("trials"), list):
            rows.extend(payload["trials"])
        elif isinstance(payload, dict) and "nct_id" in payload:
            rows.append(payload)
        elif isinstance(payload, dict):
            rows.extend(payload.values())
        else:
            raise ValueError("DATASET_A_RAW_TRIAL_SHAPE_INVALID")
    return [RawTrialRecord.model_validate(item) for item in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic structured patient worlds")
    parser.add_argument("--config", type=Path, default=Path("config/eval.yaml"))
    parser.add_argument("--mode", choices=["fixture", "release"], default="fixture")
    parser.add_argument("--compiled-trials", type=Path, action="append")
    parser.add_argument("--raw-trials", type=Path, action="append")
    parser.add_argument("--evaluation-date", type=date.fromisoformat, default=date(2026, 8, 11))
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--output", type=Path, default=Path("data/eval/generated/benchmark.json"))
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    if args.seed != int(config["seed"]):
        raise RuntimeError("SEED_MUST_MATCH_COMMITTED_EVAL_CONFIG")
    if args.mode == "release":
        if not args.compiled_trials or not args.raw_trials:
            raise RuntimeError("RELEASE_BENCHMARK_REQUIRES_COMPILED_AND_RAW_TRIALS")
        artifact = generate_dataset_a_benchmark(
            load_compiled_trials(args.compiled_trials),
            _load_raw_trials(args.raw_trials),
            seed=args.seed,
            evaluation_date=args.evaluation_date,
        )
    else:
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
