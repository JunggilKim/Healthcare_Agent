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

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.evaluation.annotations import (  # noqa: E402
    AnnotationAssignment,
    AnnotationReview,
    adjudicate_annotations,
    load_jsonl,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate independent Dataset A reviews and freeze adjudicated gold"
    )
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/eval.yaml"))
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--publish-manifest", type=Path)
    args = parser.parse_args()
    assignments = [
        AnnotationAssignment.model_validate(item.model_dump(mode="json"))
        for item in load_jsonl(args.assignments, AnnotationAssignment)
    ]
    reviews = [
        AnnotationReview.model_validate(item.model_dump(mode="json"))
        for item in load_jsonl(args.reviews, AnnotationReview)
    ]
    gold, summary = adjudicate_annotations(assignments, reviews)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    required_pairs = int(config["dataset_a"]["manual_review_subset"])
    required_dual = int(config["dataset_a"]["dual_review_subset"])
    completed_pairs = summary["completed_pairs"]
    completed_dual_reviews = summary["completed_dual_reviews"]
    incomplete = summary["incomplete"]
    assert isinstance(completed_pairs, int)
    assert isinstance(completed_dual_reviews, int)
    assert isinstance(incomplete, list)
    complete = (
        completed_pairs >= required_pairs
        and completed_dual_reviews >= required_dual
        and not incomplete
    )
    if not complete and not args.allow_incomplete:
        raise SystemExit(
            "ANNOTATION_REVIEW_INCOMPLETE:"
            f"completed={summary['completed_pairs']}/{required_pairs} "
            f"dual={summary['completed_dual_reviews']}/{required_dual} "
            f"issues={summary['incomplete']}"
        )
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty annotation directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    gold_path = output / "adjudicated.jsonl"
    write_jsonl(gold_path, gold)

    def repository_path(path: Path) -> str:
        try:
            return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
        except ValueError as exc:
            raise SystemExit(
                f"Final annotation artifacts must be inside repository: {path}"
            ) from exc

    manifest = {
        "schema_version": "trial-opt-annotation-manifest-v1",
        "status": "ADJUDICATED" if complete else "PENDING_PROJECT_REVIEW",
        "label": "protocol-text adjudication by project reviewers",
        "required_pairs": required_pairs,
        "required_dual_reviews": required_dual,
        **summary,
        "adjudicated_pairs": completed_pairs,
        "assignment_jsonl_sha256": hashlib.sha256(args.assignments.read_bytes()).hexdigest(),
        "review_jsonl_sha256": hashlib.sha256(args.reviews.read_bytes()).hexdigest(),
        "adjudicated_jsonl_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "assignment_jsonl_path": repository_path(args.assignments),
        "review_jsonl_path": repository_path(args.reviews),
        "adjudicated_jsonl_path": repository_path(gold_path),
        "rubric_version": "dataset-a-rubric-v1",
        "records": [item.record_id for item in gold],
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (output / "manifest.json").write_bytes(manifest_bytes)
    if args.publish_manifest is not None:
        if not complete:
            raise SystemExit("Refusing to publish an incomplete annotation manifest")
        publish_path = args.publish_manifest.resolve()
        repository_path(publish_path)
        publish_path.parent.mkdir(parents=True, exist_ok=True)
        publish_path.write_bytes(manifest_bytes)
    print(orjson.dumps({"output": str(output), **manifest}).decode())


if __name__ == "__main__":
    main()
