from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.agents.prompts import prompt_sha256  # noqa: E402
from backend.app.agents.protocol_compiler import opaque_fallback_compilation  # noqa: E402
from backend.app.application.catalog import load_slot_catalog  # noqa: E402
from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.infrastructure.local_artifacts import LocalArtifactStore  # noqa: E402
from backend.app.retrieval.ctgov_client import ClinicalTrialsGovClient  # noqa: E402
from backend.app.retrieval.embeddings import RecordedEmbeddingProvider  # noqa: E402
from backend.app.retrieval.models import RetrievalQuery  # noqa: E402
from backend.app.retrieval.retriever import HybridRetriever  # noqa: E402


def _write(path: Path, value: object) -> str:
    content = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


async def build(output: Path) -> None:
    source_root = REPOSITORY_ROOT / "data" / "fixtures" / "retrieval" / "S004"
    full_manifest = orjson.loads((source_root / "manifest.full.json").read_bytes())
    query = RetrievalQuery.model_validate(full_manifest["retrieval_query"])
    retriever = HybridRetriever(
        ctgov=ClinicalTrialsGovClient(
            LocalArtifactStore(REPOSITORY_ROOT / ".local_store" / "phase3-build")
        ),
        embeddings=RecordedEmbeddingProvider(source_root / "embeddings.json"),
        snapshot_root=source_root,
    )
    retrieval = await retriever.retrieve(query, mode="snapshot")
    now = datetime(2026, 8, 11, 9, 0, 6, tzinfo=UTC)
    evaluation_date = date(2026, 8, 11)
    entries: list[dict[str, object]] = []
    for candidate in retrieval.ranked_candidates[:8]:
        result = opaque_fallback_compilation(
            trial=candidate.trial,
            slot_catalog=load_slot_catalog(),
            created_at=now,
            evaluation_date=evaluation_date,
            reason_code="PHASE3_RECORDED_MODEL_OUTPUT_UNAVAILABLE",
        )
        nct_id = candidate.nct_id
        compiled_path = output / f"{nct_id}.compiled.json"
        report_path = output / f"{nct_id}.coverage.json"
        compiled_hash = _write(compiled_path, result.compiled_trial)
        report_hash = _write(
            report_path,
            {
                "nct_id": nct_id,
                "coverage": asdict(result.coverage_report),
                "boundary_reports": {
                    criterion_id: asdict(report)
                    for criterion_id, report in result.boundary_reports.items()
                },
                "status": "OPAQUE_REVIEW_REQUIRED",
                "hard_verdict_allowed": False,
            },
        )
        entries.append(
            {
                "nct_id": nct_id,
                "compiled_path": compiled_path.name,
                "compiled_sha256": compiled_hash,
                "report_path": report_path.name,
                "report_sha256": report_hash,
                "protocol_verified": False,
                "opaque": True,
            }
        )
    _write(
        output / "manifest.json",
        {
            "version": "phase3-s004-top8-v1",
            "source_retrieval_snapshot": full_manifest["snapshot_version"],
            "source_search_sha256": full_manifest["search_response"]["sha256"],
            "generated_at": now,
            "model_execution": "not_run_no_external_credentials",
            "prompt_hashes": {
                name: prompt_sha256(name)
                for name in [
                    "patient_extraction_v1.md",
                    "retrieval_query_v1.md",
                    "protocol_compiler_v1.md",
                    "protocol_reviewer_v1.md",
                ]
            },
            "entries": entries,
        },
    )


def main() -> None:
    asyncio.run(build(REPOSITORY_ROOT / "data" / "fixtures" / "compiled" / "S004"))


if __name__ == "__main__":
    main()
