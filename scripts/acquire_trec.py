from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import orjson

DATASET_ID = "clinicaltrials/2021/trec-ct-2022"


def _not_run(output: Path, code: str, prerequisite: str, instructions: list[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "trial-opt-trec-not-run-v1",
        "dataset_id": DATASET_ID,
        "status": "NOT_RUN",
        "missing_prerequisite_code": code,
        "missing_prerequisite": prerequisite,
        "acquisition_instructions": instructions,
        "release_blocking": False,
        "reason_release_non_blocking": (
            "The mandatory retrieval gate uses Dataset A; fabricated TREC scores are forbidden."
        ),
    }
    (output / "not_run.json").write_bytes(
        orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize the exact TREC 2022 CT adapter")
    parser.add_argument("--output", type=Path, default=Path("artifacts/eval/trec2022"))
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--licensed-corpus-root", type=Path)
    args = parser.parse_args()
    if importlib.util.find_spec("ir_datasets") is None:
        _not_run(
            args.output,
            "MISSING_IR_DATASETS_PACKAGE",
            "The optional ir_datasets package is not installed in the release environment.",
            [
                "Install ir_datasets in a separate licensed-data evaluation environment.",
                f"Load exactly ir_datasets.load('{DATASET_ID}').",
                "Provide the frozen 2021 ClinicalTrials.gov corpus required by the adapter.",
                "Rerun this command with --materialize and --licensed-corpus-root.",
            ],
        )
        return
    if not args.materialize or args.licensed_corpus_root is None:
        _not_run(
            args.output,
            "MISSING_LICENSED_2021_CORPUS",
            "The frozen 2021 ClinicalTrials.gov corpus was not supplied locally.",
            [
                "Follow https://ir-datasets.com/clinicaltrials.html for corpus setup.",
                "Confirm local licensing and storage before materialization.",
                "Rerun with --materialize --licensed-corpus-root PATH.",
            ],
        )
        return

    import ir_datasets  # type: ignore[import-not-found]

    dataset = ir_datasets.load(DATASET_ID)
    args.output.mkdir(parents=True, exist_ok=True)
    topics = [topic._asdict() for topic in dataset.queries_iter()]
    qrels = [qrel._asdict() for qrel in dataset.qrels_iter()]
    (args.output / "topics.json").write_bytes(orjson.dumps(topics, option=orjson.OPT_SORT_KEYS))
    (args.output / "qrels.json").write_bytes(orjson.dumps(qrels, option=orjson.OPT_SORT_KEYS))
    corpus_marker = {
        "dataset_id": DATASET_ID,
        "status": "TOPICS_QRELS_MATERIALIZED",
        "licensed_corpus_root": str(args.licensed_corpus_root.resolve()),
        "document_count": dataset.docs_count(),
    }
    (args.output / "manifest.json").write_bytes(
        orjson.dumps(corpus_marker, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    )


if __name__ == "__main__":
    main()
