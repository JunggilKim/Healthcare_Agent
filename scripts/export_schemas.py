from __future__ import annotations

import sys
from pathlib import Path

import orjson

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.model_outputs import (  # noqa: E402
    CompiledTrialProposal,
    PatientExtractionResult,
    ProtocolReviewProposal,
)
from backend.app.retrieval.models import RetrievalQuery  # noqa: E402


def main() -> None:
    output = REPOSITORY_ROOT / "schemas"
    output.mkdir(parents=True, exist_ok=True)
    models = {
        "patient_extraction.schema.json": PatientExtractionResult,
        "retrieval_query.schema.json": RetrievalQuery,
        "compiled_trial_proposal.schema.json": CompiledTrialProposal,
        "protocol_review.schema.json": ProtocolReviewProposal,
    }
    for filename, model in models.items():
        (output / filename).write_bytes(
            orjson.dumps(model.model_json_schema(), option=orjson.OPT_SORT_KEYS)
        )


if __name__ == "__main__":
    main()
