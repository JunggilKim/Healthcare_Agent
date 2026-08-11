from __future__ import annotations

import json

import orjson
from fastapi import APIRouter

from backend.app.infrastructure.local_artifacts import LocalArtifactStore
from backend.app.infrastructure.snapshot_loader import (
    SnapshotIntegrityError,
    load_verified_snapshot,
)
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient
from backend.app.retrieval.embeddings import RecordedEmbeddingProvider
from backend.app.retrieval.models import RetrievalQuery, RetrievalResult
from backend.app.retrieval.retriever import HybridRetriever
from backend.app.settings import REPOSITORY_ROOT, get_settings

router = APIRouter(tags=["demo"])


@router.get("/demo/cases")
async def demo_cases() -> dict[str, object]:
    path = REPOSITORY_ROOT / "data" / "seeds" / "synthetic-patients.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    complete_cases = {"S004"}
    try:
        manifest = load_verified_snapshot(get_settings().demo_snapshot_dir, require_complete=True)
        complete_cases.update(case.case_id for case in manifest.cases if case.complete)
    except (SnapshotIntegrityError, ValueError):
        pass
    return {
        "cases": [
            {
                "id": item["num"],
                "text": item["title"],
                "has_full_snapshot": item["num"] in complete_cases,
            }
            for item in payload["topics"]
        ]
    }


@router.get("/demo/retrieval/S004", response_model=RetrievalResult)
async def s004_retrieval() -> RetrievalResult:
    root = REPOSITORY_ROOT / "data" / "fixtures" / "retrieval" / "S004"
    manifest = orjson.loads((root / "manifest.full.json").read_bytes())
    query = RetrievalQuery.model_validate(manifest["retrieval_query"])
    retriever = HybridRetriever(
        ctgov=ClinicalTrialsGovClient(
            LocalArtifactStore(REPOSITORY_ROOT / ".local_store" / "retrieval")
        ),
        embeddings=RecordedEmbeddingProvider(root / "embeddings.json"),
        snapshot_root=root,
    )
    result = await retriever.retrieve(query, mode="snapshot")
    compiled_manifest = orjson.loads(
        (REPOSITORY_ROOT / "data" / "fixtures" / "compiled" / "S004" / "manifest.json").read_bytes()
    )
    opaque_ids = {entry["nct_id"] for entry in compiled_manifest["entries"]}
    candidates = [
        candidate.model_copy(
            update={
                "compiled": candidate.nct_id in opaque_ids,
                "compilation_status": (
                    "OPAQUE_REVIEW_REQUIRED" if candidate.nct_id in opaque_ids else "NOT_COMPILED"
                ),
            }
        )
        for candidate in result.ranked_candidates
    ]
    return result.model_copy(update={"ranked_candidates": candidates})
