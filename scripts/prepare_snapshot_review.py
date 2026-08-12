from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import yaml

CASE_IDS = ("S004", "S008", "S001")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare exact-hash review checklist for a materialized live snapshot"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("OUTPUT_MUST_NOT_EXIST")
    cases = []
    for case_id in CASE_IDS:
        case_root = args.source / "sessions" / case_id
        compiled = case_root / "compiled_trials.json"
        proofs = case_root / "proofs.json"
        questions = case_root / "questions.json"
        for path in (compiled, proofs, questions):
            if not path.is_file():
                raise RuntimeError(f"SNAPSHOT_REVIEW_INPUT_MISSING:{case_id}:{path.name}")
        cases.append(
            {
                "case_id": case_id,
                "approved": False,
                "reviewer_alias": None,
                "reviewed_at": None,
                "compiled_trials_sha256": _sha256(compiled),
                "proofs_sha256": _sha256(proofs),
                "questions_sha256": _sha256(questions),
                "checks": {
                    "trial_relevance_and_status_screened": False,
                    "source_quotes_and_compiled_criteria_inspected": False,
                    "hard_verdicts_have_approved_semantic_review": False,
                    "selected_question_and_branches_inspected": False,
                },
                "notes": None,
            }
        )
    payload = {
        "schema_version": "trial-opt-manual-review-v1",
        "status": "PENDING_EXTERNAL_REVIEW",
        "review_policy": "exact_hash_case_review",
        "review_label": "protocol-text adjudication by project reviewers",
        "instructions": (
            "Inspect the bound artifacts, set every check and approved to true, then add a "
            "non-identifying reviewer_alias and ISO-8601 reviewed_at. Set top-level status to "
            "APPROVED only after all three cases pass. Do not edit artifact hashes."
        ),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
