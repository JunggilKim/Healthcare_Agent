from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import orjson
import yaml

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.infrastructure.snapshot_loader import load_verified_snapshot
from scripts.build_demo_snapshot import REQUIRED_CASE_ARTIFACTS


def _write_live_source(root: Path) -> Path:
    artifact_hashes: dict[str, str] = {}
    review_cases = []
    for case_id in ("S004", "S008", "S001"):
        case_root = root / "sessions" / case_id
        case_root.mkdir(parents=True)
        first_branch = f"{case_id}:q1:branch:0"
        initial = {"current_question": {"selected": {"branches": [{"branch_id": first_branch}]}}}
        questions = {
            "branches": [
                {
                    "branch_id": first_branch,
                    "question_id": f"{case_id}:q1",
                    "depth": 1,
                    "artifact_path": "branches/q1-b0.json",
                },
                *(
                    [
                        {
                            "branch_id": f"{case_id}:q2:branch:0",
                            "question_id": f"{case_id}:q2",
                            "depth": 2,
                            "artifact_path": "branches/q2-b0.json",
                        }
                    ]
                    if case_id == "S004"
                    else []
                ),
            ]
        }
        for name in REQUIRED_CASE_ARTIFACTS:
            path = case_root / name
            if name == "initial.json":
                content = canonical_json_bytes(initial)
            elif name == "questions.json":
                content = canonical_json_bytes(questions)
            elif name == "embeddings.npz":
                content = b"recorded-npz-fixture"
            elif name in {"compiled_trials.json", "raw_trials.json"}:
                case_offset = {"S004": 0, "S008": 8, "S001": 16}[case_id]
                content = canonical_json_bytes(
                    {
                        "trials": [
                            {"nct_id": f"NCT{case_offset + index + 1:08d}"} for index in range(8)
                        ]
                    }
                )
            else:
                content = canonical_json_bytes({"case_id": case_id, "artifact": name})
            path.write_bytes(content)
        branch_root = case_root / "branches"
        branch_root.mkdir()
        (branch_root / "q1-b0.json").write_bytes(canonical_json_bytes({"depth": 1}))
        if case_id == "S004":
            (branch_root / "q2-b0.json").write_bytes(canonical_json_bytes({"depth": 2}))
        for path in case_root.rglob("*"):
            if path.is_file():
                artifact_hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        review_cases.append(
            {
                "case_id": case_id,
                "approved": True,
                "reviewer_alias": f"reviewer-{case_id}",
                "reviewed_at": "2026-08-12T00:00:00Z",
                "compiled_trials_sha256": hashlib.sha256(
                    (case_root / "compiled_trials.json").read_bytes()
                ).hexdigest(),
                "proofs_sha256": hashlib.sha256(
                    (case_root / "proofs.json").read_bytes()
                ).hexdigest(),
            }
        )
    (root / "acquisition.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "trial-opt-live-acquisition-v1",
                "mode": "LIVE",
                "case_ids": ["S004", "S008", "S001"],
                "data_timestamp": "2026-08-12T00:00:00Z",
                "artifact_sha256": artifact_hashes,
            }
        )
    )
    review_path = root / "manual_review.yaml"
    review_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "trial-opt-manual-review-v1",
                "status": "APPROVED",
                "cases": review_cases,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return review_path


def test_live_snapshot_command_freezes_hash_bound_acquisition(tmp_path: Path) -> None:
    source = tmp_path / "prepared"
    review = _write_live_source(source)
    output = tmp_path / "current"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_demo_snapshot.py",
            "--cases",
            "S004,S008,S001",
            "--mode",
            "live",
            "--manual-review-manifest",
            str(review),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = load_verified_snapshot(output)
    assert manifest.data_timestamp == "2026-08-12T00:00:00Z"
    assert [case.case_id for case in manifest.cases] == ["S004", "S008", "S001"]
    assert "acquisition.json" in {item.path for item in manifest.files}
    acquisition = orjson.loads((output / "acquisition.json").read_bytes())
    assert acquisition["mode"] == "LIVE"
