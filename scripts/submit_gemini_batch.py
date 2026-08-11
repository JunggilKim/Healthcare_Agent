from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import orjson
import yaml
from google.genai import types

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.infrastructure.genai_client import create_google_cloud_genai_client  # noqa: E402
from backend.app.settings import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit one explicitly approved Vertex AI batch")
    parser.add_argument("--model-role", choices=["primary", "lite"], required=True)
    parser.add_argument("--input-gcs-uri", required=True)
    parser.add_argument("--output-gcs-uri", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--allow-paid-batch", action="store_true")
    args = parser.parse_args()
    if not args.allow_paid_batch or os.getenv("ALLOW_PAID_BATCH_CALLS") != "true":
        raise SystemExit(
            "Paid batch submission requires --allow-paid-batch and ALLOW_PAID_BATCH_CALLS=true"
        )
    if not args.input_gcs_uri.startswith("gs://") or not args.output_gcs_uri.startswith("gs://"):
        raise SystemExit("Batch input and output must be gs:// URIs")
    models = yaml.safe_load((REPOSITORY_ROOT / "config" / "models.yaml").read_text())
    model_id = str(models["models"][args.model_role]["id"])
    client = create_google_cloud_genai_client(get_settings())
    job = client.batches.create(
        model=model_id,
        src=types.BatchJobSource(format="jsonl", gcs_uri=[args.input_gcs_uri]),
        config=types.CreateBatchJobConfig(
            display_name=args.display_name,
            dest=types.BatchJobDestination(format="jsonl", gcs_uri=args.output_gcs_uri),
        ),
    )
    print(
        orjson.dumps(
            {
                "name": job.name,
                "state": None if job.state is None else job.state.value,
                "model": model_id,
                "input_gcs_uri": args.input_gcs_uri,
                "output_gcs_uri": args.output_gcs_uri,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
