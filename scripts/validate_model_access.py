from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import yaml
from google.genai import types

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.domain.canonical import canonical_json_bytes  # noqa: E402
from backend.app.infrastructure.genai_client import (  # noqa: E402
    create_google_cloud_genai_client,
)
from backend.app.settings import Settings  # noqa: E402


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _model_ids(config_path: Path) -> tuple[list[str], int]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    models = config["models"]
    ids = [models["primary"]["id"], models["lite"]["id"], models["embedding"]["id"]]
    if len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("configured model IDs must be unique nonempty strings")
    return ids, int(models["embedding"]["dimensions"])


def _usage(response: Any) -> dict[str, int | None]:
    metadata = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(metadata, "prompt_token_count", None),
        "output_tokens": getattr(metadata, "candidates_token_count", None),
        "total_tokens": getattr(metadata, "total_token_count", None),
    }


def run_probes(*, project: str, location: str, config_path: Path) -> dict[str, object]:
    model_ids, embedding_dimensions = _model_ids(config_path)
    primary, lite, embedding = model_ids
    client = create_google_cloud_genai_client(
        Settings(
            google_cloud_project=project,
            google_cloud_location=location,
            allow_live_model_calls=True,
        )
    )
    probes: list[dict[str, object]] = []
    generation_probes = ((primary, "MEDIUM"), (lite, "LOW"))
    for model_id, thinking_level in generation_probes:
        response = client.models.generate_content(
            model=model_id,
            contents="Return exactly ACCESS_OK.",
            config=types.GenerateContentConfig(
                # Thinking tokens share the output-token budget. A 16-token probe can
                # therefore prove routing while returning an empty visible response.
                max_output_tokens=256,
                temperature=0,
                response_mime_type="text/plain",
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel(thinking_level)
                ),
            ),
        )
        if not response.text:
            raise RuntimeError(f"MODEL_ACCESS_EMPTY_RESPONSE:{model_id}")
        probes.append(
            {
                "model_id": model_id,
                "probe_type": "GENERATE_CONTENT",
                "thinking_level": thinking_level,
                "response_nonempty": True,
                "usage": _usage(response),
            }
        )
    response = client.models.embed_content(
        model=embedding,
        contents="TRIAL-OPT model access probe",
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=embedding_dimensions,
        ),
    )
    dimensions = len(response.embeddings[0].values or []) if response.embeddings else 0
    if dimensions != embedding_dimensions:
        raise RuntimeError(
            f"MODEL_ACCESS_EMBEDDING_DIMENSION_MISMATCH:{dimensions}:{embedding_dimensions}"
        )
    probes.append(
        {
            "model_id": embedding,
            "probe_type": "EMBED_CONTENT",
            "embedding_dimensions": dimensions,
            "usage": _usage(response),
        }
    )
    return {"models": model_ids, "probes": probes}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one minimal first-party v1 access probe for each frozen release model"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="global")
    parser.add_argument("--config", type=Path, default=REPOSITORY_ROOT / "config" / "models.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "release" / "model_access_validation.json",
    )
    args = parser.parse_args()
    if os.environ.get("ALLOW_LIVE_MODEL_CALLS", "").lower() != "true":
        raise SystemExit("Refusing paid model probes unless ALLOW_LIVE_MODEL_CALLS=true")
    if args.location != "global":
        raise SystemExit("The frozen release model endpoint location is global")

    common = {
        "schema_version": "trial-opt-model-access-validation-v1",
        "git_sha": _git_sha(),
        "project_id": args.project,
        "location": args.location,
        "api_version": "v1",
        "provider": "google_cloud_first_party",
        "consumption": "STANDARD_PAYGO",
        "validated_at": datetime.now(UTC).isoformat(),
        "google_genai_version": importlib.metadata.version("google-genai"),
        "models_config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
    }
    try:
        evidence = {
            **common,
            **run_probes(project=args.project, location=args.location, config_path=args.config),
            "passed": True,
        }
    except Exception as error:
        evidence = {
            **common,
            "models": _model_ids(args.config)[0],
            "probes": [],
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(evidence))
    print(orjson.dumps({"output": str(args.output), "passed": evidence["passed"]}).decode())
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
