from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.domain.trials import RawTrialRecord  # noqa: E402
from backend.app.infrastructure.genai_client import (  # noqa: E402
    create_google_cloud_genai_client,
)
from backend.app.infrastructure.local_artifacts import LocalArtifactStore  # noqa: E402
from backend.app.retrieval.ctgov_client import (  # noqa: E402
    ClinicalTrialsGovClient,
    CtgovResponse,
)
from backend.app.retrieval.ctgov_parser import parse_study, validate_single_study  # noqa: E402
from backend.app.retrieval.embeddings import GeminiEmbeddingProvider  # noqa: E402
from backend.app.retrieval.models import ConditionQuery, RetrievalQuery  # noqa: E402
from backend.app.retrieval.retriever import HybridRetriever  # noqa: E402
from backend.app.settings import Settings  # noqa: E402

CASE_QUERIES = {
    "S004": (
        [
            "outcomes high-risk non-muscle invasive bladder cancer blue light resection",
            "high risk non-muscle invasive bladder cancer",
            "urothelial carcinoma",
            "localized muscle invasive bladder urothelial carcinoma",
        ],
        "bladder cancer urothelial carcinoma",
    ),
    "S008": (
        ["idiopathic pulmonary fibrosis", "interstitial lung disease"],
        "interstitial lung disease pulmonary fibrosis honeycombing",
    ),
    "S001": (["acute pancreatitis"], "acute pancreatitis elevated lipase amylase"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def acquire(
    *, project: str, output: Path, trials_per_case: int, candidate_pool: bool = False
) -> None:
    if output.exists():
        raise RuntimeError("OUTPUT_MUST_BE_A_FRESH_DIRECTORY")
    output.mkdir(parents=True)
    cache = LocalArtifactStore(REPOSITORY_ROOT / ".local_store" / "release-corpus")
    client = create_google_cloud_genai_client(
        Settings(google_cloud_project=project, google_cloud_location="global")
    )
    ctgov = ClinicalTrialsGovClient(cache)
    retriever = HybridRetriever(
        ctgov=ctgov,
        embeddings=GeminiEmbeddingProvider(client, model="gemini-embedding-001", dimension=768),
        snapshot_root=REPOSITORY_ROOT / "data" / "fixtures" / "retrieval" / "S004",
    )
    used_nct_ids: set[str] = set()
    case_manifests: list[dict[str, object]] = []
    all_artifact_hashes: dict[str, str] = {}
    semaphore = asyncio.Semaphore(4)

    for case_id, (condition_texts, dense_query) in CASE_QUERIES.items():
        query = RetrievalQuery(
            condition_queries=[
                ConditionQuery(text=text, priority=index + 1)
                for index, text in enumerate(condition_texts)
            ],
            dense_query=dense_query,
            must_not_use_as_eligibility_evidence=True,
        )
        retrieval = await retriever.retrieve(query, mode="live", allow_snapshot_fallback=False)
        if not retrieval.dense_source_used or retrieval.mode != "live":
            raise RuntimeError(f"RELEASE_RETRIEVAL_DENSE_SOURCE_REQUIRED:{case_id}")
        selected = [
            item
            for item in retrieval.ranked_candidates
            if item.nct_id not in used_nct_ids and item.trial.eligibility_criteria
        ][:trials_per_case]
        if len(selected) != trials_per_case:
            raise RuntimeError(f"RELEASE_CASE_TRIAL_COUNT_INSUFFICIENT:{case_id}:{len(selected)}")
        used_nct_ids.update(item.nct_id for item in selected)

        async def fetch_full(nct_id: str) -> tuple[CtgovResponse, RawTrialRecord]:
            async with semaphore:
                response = await ctgov.study(nct_id)
            study = validate_single_study(response.content)
            trial = parse_study(
                study,
                api_version=response.api_version,
                retrieved_at=response.retrieved_at,
                raw_bytes=response.content,
            )
            return response, trial

        full = await asyncio.gather(*(fetch_full(item.nct_id) for item in selected))
        case_root = output / "sessions" / case_id
        raw_api_root = case_root / "raw_api"
        raw_api_root.mkdir(parents=True)
        raw_trials = []
        screening_rows = []
        by_nct_id = {item.nct_id: item for item in retrieval.ranked_candidates}
        for response, trial in full:
            raw_path = raw_api_root / f"{trial.nct_id}.json"
            raw_path.write_bytes(response.content)
            relative = raw_path.relative_to(output).as_posix()
            all_artifact_hashes[relative] = _sha256(raw_path)
            raw_trials.append(trial.model_dump(mode="json"))
            candidate = by_nct_id[trial.nct_id]
            screening_rows.append(
                {
                    "schema_version": "trial-opt-corpus-screening-assignment-v1",
                    "case_id": case_id,
                    "nct_id": trial.nct_id,
                    "registry_rank": candidate.registry_rank,
                    "bm25_rank": candidate.bm25_rank,
                    "embedding_rank": candidate.embedding_rank,
                    "retrieval_score": candidate.retrieval_score,
                    "title": trial.brief_title,
                    "conditions": trial.conditions,
                    "overall_status": trial.overall_status,
                    "study_type": trial.study_type,
                    "eligibility_criteria": trial.eligibility_criteria,
                    "source_json_sha256": trial.source_json_sha256,
                    "reviewer_alias": None,
                    "reviewed_at": None,
                    "relevant_to_seed_case": None,
                    "safe_for_protocol_text_adjudication": None,
                    "review_notes": None,
                }
            )

        paths = {
            "retrieval.json": canonical_json_bytes(retrieval.model_dump(mode="json")),
            "raw_trials.json": canonical_json_bytes(raw_trials),
            "screening_assignments.jsonl": b"\n".join(
                canonical_json_bytes(row) for row in screening_rows
            )
            + b"\n",
        }
        for name, content in paths.items():
            path = case_root / name
            path.write_bytes(content)
            all_artifact_hashes[path.relative_to(output).as_posix()] = _sha256(path)
        case_manifests.append(
            {
                "case_id": case_id,
                "condition_queries": condition_texts,
                "dense_query": dense_query,
                "api_version": retrieval.api_version,
                "data_timestamp": retrieval.registry_data_timestamp,
                "selected_nct_ids": [item.nct_id for item in selected],
                "trial_count": len(selected),
                "screening_status": "PENDING_PROJECT_REVIEW",
            }
        )
        print(orjson.dumps({"case_id": case_id, "selected": len(selected)}).decode())

    maximum = 60 if candidate_pool else 36
    if not 24 <= len(used_nct_ids) <= maximum:
        raise RuntimeError(f"RELEASE_ACQUISITION_TRIAL_COUNT_INVALID:{len(used_nct_ids)}")
    manifest = {
        "schema_version": "trial-opt-live-corpus-acquisition-v1",
        "status": (
            "PENDING_PROJECT_SCREENING_CANDIDATE_POOL"
            if candidate_pool
            else "PENDING_PROJECT_SCREENING"
        ),
        "mode": "LIVE",
        "project_id": project,
        "git_sha": _git_sha(),
        "acquired_at": datetime.now(UTC).isoformat(),
        "case_ids": list(CASE_QUERIES),
        "unique_trial_count": len(used_nct_ids),
        "candidate_pool": candidate_pool,
        "embedding_model": "gemini-embedding-001",
        "embedding_dimensions": 768,
        "cases": case_manifests,
        "artifact_sha256": dict(sorted(all_artifact_hashes.items())),
    }
    (output / "acquisition.json").write_bytes(canonical_json_bytes(manifest))
    print(
        orjson.dumps(
            {
                "output": str(output),
                "unique_trial_count": len(used_nct_ids),
                "status": manifest["status"],
            }
        ).decode()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Acquire and hash-freeze the three-domain release trial screening corpus"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials-per-case", type=int, default=8, choices=range(8, 21))
    parser.add_argument(
        "--candidate-pool",
        action="store_true",
        help="allow a screening pool above the final 12-per-case release cap",
    )
    parser.add_argument("--allow-live-acquisition", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_acquisition:
        raise SystemExit("Refusing external acquisition without --allow-live-acquisition")
    if os.environ.get("ALLOW_LIVE_CTGOV_CALLS", "").lower() != "true":
        raise SystemExit("ALLOW_LIVE_CTGOV_CALLS=true is required")
    if os.environ.get("ALLOW_LIVE_MODEL_CALLS", "").lower() != "true":
        raise SystemExit("ALLOW_LIVE_MODEL_CALLS=true is required for dense retrieval")
    asyncio.run(
        acquire(
            project=args.project,
            output=args.output.resolve(),
            trials_per_case=args.trials_per_case,
            candidate_pool=args.candidate_pool,
        )
    )


if __name__ == "__main__":
    main()
