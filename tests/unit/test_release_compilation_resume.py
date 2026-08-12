from __future__ import annotations

import hashlib
from pathlib import Path

import orjson

from scripts.compile_release_corpus import SCHEMA_VERSION, _is_resumable


def test_resume_accepts_valid_artifact_created_with_smaller_chunk_size(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b"{}")
    result = tmp_path / "result.json"
    shared = {
        "nct_id": "NCT00000001",
        "source_json_sha256": "a" * 64,
        "evaluation_date": "2026-08-11",
        "models_config_sha256": "b" * 64,
        "slots_config_sha256": "c" * 64,
        "implementation_sha256": "d" * 64,
    }
    result.write_bytes(
        orjson.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "binding": {
                    **shared,
                    "compiler_chunk_size": 2,
                    "reviewer_chunk_size": 1,
                    "ast_schema_version": "criterion-ast-v1",
                },
                "artifact_sha256": {
                    "artifact.json": hashlib.sha256(artifact.read_bytes()).hexdigest()
                },
            }
        )
    )
    requested = {
        **shared,
        "compiler_chunk_size": 4,
        "reviewer_chunk_size": 4,
        "ast_schema_version": "criterion-ast-v1",
    }
    assert _is_resumable(result, requested, tmp_path)
    assert not _is_resumable(
        result,
        {**requested, "source_json_sha256": "e" * 64},
        tmp_path,
    )
