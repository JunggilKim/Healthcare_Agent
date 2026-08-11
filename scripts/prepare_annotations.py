from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.annotations import (  # noqa: E402
    build_annotation_assignments,
    load_compiled_trials,
    write_jsonl,
)
from backend.app.evaluation.models import BenchmarkArtifact  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare blinded, hash-bound Dataset A annotation assignments"
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--compiled-trials", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--dual-review-size", type=int, default=50)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty annotation directory: {output}")
    benchmark = BenchmarkArtifact.model_validate(orjson.loads(args.benchmark.read_bytes()))
    compiled_trials = load_compiled_trials(args.compiled_trials)
    assignments = build_annotation_assignments(
        benchmark,
        compiled_trials,
        seed=args.seed,
        sample_size=args.sample_size,
        dual_review_size=args.dual_review_size,
        complete_world_bundles=True,
    )
    assignment_path = output / "assignments.jsonl"
    write_jsonl(assignment_path, assignments)
    manifest = {
        "schema_version": "trial-opt-annotation-assignment-manifest-v1",
        "status": "READY_FOR_BLINDED_REVIEW",
        "label": "protocol-text adjudication by project reviewers",
        "benchmark_sha256": hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        "compiled_trial_sha256": {
            path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in args.compiled_trials
        },
        "assignment_jsonl_sha256": hashlib.sha256(assignment_path.read_bytes()).hexdigest(),
        "seed": args.seed,
        "assignment_count": len(assignments),
        "selection_contract": "complete patient-world criterion bundles; target size is a minimum",
        "dual_review_count": sum(item.dual_review_required for item in assignments),
        "split_nct_ids": {
            split: sorted({item.nct_id for item in assignments if item.split == split})
            for split in ("development", "validation", "test")
        },
        "rubric_version": "dataset-a-rubric-v1",
        "blinding_contract": "system predictions and generated truth are absent from assignments",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "assignment_manifest.json").write_bytes(canonical_json_bytes(manifest))
    print(orjson.dumps({"output": str(output), **manifest}).decode())


if __name__ == "__main__":
    main()
