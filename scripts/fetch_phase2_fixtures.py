from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.retrieval.bm25 import build_trial_document  # noqa: E402
from backend.app.retrieval.ctgov_client import (  # noqa: E402
    CTGOV_BASE_URL,
    CTGOV_USER_AGENT,
    SEARCH_FIELDS,
    STATUS_FILTER,
)
from backend.app.retrieval.ctgov_parser import parse_study, validate_study_page  # noqa: E402


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _fixture_vector(task_type: str, text: str, dimension: int = 16) -> list[float]:
    """Deterministic recorded test vectors, explicitly not model-generated embeddings."""
    digest = hashlib.sha256(f"{task_type}\0{text}".encode()).digest()
    return [round((digest[index] - 127.5) / 127.5, 8) for index in range(dimension)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/fixtures/retrieval/S004"))
    args = parser.parse_args()
    headers = {"User-Agent": CTGOV_USER_AGENT, "Accept": "application/json"}
    timeout = httpx.Timeout(10.0, connect=3.0)
    with httpx.Client(base_url=CTGOV_BASE_URL, headers=headers, timeout=timeout) as client:
        version_response = client.get("/version")
        version_response.raise_for_status()
        version = version_response.json()
        search_response = client.get(
            "/studies",
            params={
                "query.cond": "bladder cancer",
                "filter.overallStatus": STATUS_FILTER,
                "pageSize": 100,
                "countTotal": "true",
                "fields": SEARCH_FIELDS,
            },
        )
        search_response.raise_for_status()
        complete_response = client.get("/studies/NCT05239624")
        complete_response.raise_for_status()

    search_content = search_response.content
    complete_content = complete_response.content
    validate_study_page(search_content)
    search_hash = _write(args.output / "search_response.json", search_content)
    complete_hash = _write(args.output / "NCT05239624.raw.json", complete_content)

    dense_query = (
        "bladder cancer; demographics age; demographics sex; "
        "imaging bladder wall mass; symptom gross hematuria"
    )
    vectors: dict[str, list[float]] = {}
    query_key = hashlib.sha256(f"RETRIEVAL_QUERY\0{dense_query}".encode()).hexdigest()
    vectors[query_key] = _fixture_vector("RETRIEVAL_QUERY", dense_query)
    now = datetime.now(UTC)
    for study in validate_study_page(search_content):
        study_bytes = orjson.dumps(study, option=orjson.OPT_SORT_KEYS)
        trial = parse_study(
            study,
            api_version=version["apiVersion"],
            retrieved_at=now,
            raw_bytes=study_bytes,
        )
        document = build_trial_document(trial)
        key = hashlib.sha256(f"RETRIEVAL_DOCUMENT\0{document}".encode()).hexdigest()
        vectors[key] = _fixture_vector("RETRIEVAL_DOCUMENT", document)
    embeddings = orjson.dumps(
        {
            "model": "recorded-deterministic-test-fixture-v1",
            "dimension": 16,
            "source": "not_model_generated",
            "vectors": vectors,
        },
        option=orjson.OPT_SORT_KEYS,
    )
    embeddings_hash = _write(args.output / "embeddings.json", embeddings)

    manifest = orjson.dumps(
        {
            "snapshot_version": f"phase2-{version['dataTimestamp'][:10]}",
            "api_version": version["apiVersion"],
            "data_timestamp": version["dataTimestamp"],
            "case_id": "S004",
            "search_response": {"path": "search_response.json", "sha256": search_hash},
            "complete_record": {
                "path": "NCT05239624.raw.json",
                "sha256": complete_hash,
            },
            "recorded_embeddings": {
                "path": "embeddings.json",
                "sha256": embeddings_hash,
            },
            "retrieval_query": {
                "condition_queries": [
                    {
                        "text": "bladder cancer",
                        "source_fact_ids": [],
                        "source_hypothesis_ids": ["hyp_00000000-0000-4000-8000-000000000001"],
                        "priority": 1,
                    }
                ],
                "dense_query": dense_query,
                "must_not_use_as_eligibility_evidence": True,
            },
        },
        option=orjson.OPT_SORT_KEYS,
    )
    _write(args.output / "manifest.full.json", manifest)
    snapshot_manifest = orjson.dumps(
        {
            "snapshot_version": f"phase2-{version['dataTimestamp'][:10]}",
            "api_version": version["apiVersion"],
            "data_timestamp": version["dataTimestamp"],
            "case_id": "S004",
            "search_response": {"path": "search_response.json", "sha256": search_hash},
        },
        option=orjson.OPT_SORT_KEYS,
    )
    _write(args.output / "manifest.json", snapshot_manifest)


if __name__ == "__main__":
    main()
