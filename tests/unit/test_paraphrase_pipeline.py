from __future__ import annotations

import json

import orjson
import pytest

from backend.app.domain.model_outputs import PatientExtractionResult, PatientFactProposal
from backend.app.evaluation.models import BenchmarkArtifact
from backend.app.evaluation.paraphrases import (
    apply_validated_paraphrases,
    build_extraction_requests,
    build_paraphrase_requests,
    inline_json_schema_references,
    paraphrase_candidate_worlds,
    parse_paraphrase_responses,
    patient_extraction_batch_response_schema,
    select_paraphrase_worlds,
)


def test_batch_json_schema_inlines_pydantic_references() -> None:
    schema = {
        "$defs": {"Item": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "type": "array",
        "items": {"$ref": "#/$defs/Item"},
    }
    inlined = inline_json_schema_references(schema)
    assert inlined["items"] == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    assert "$defs" not in orjson.dumps(inlined).decode()
    assert "$ref" not in orjson.dumps(inlined).decode()


def _benchmark() -> BenchmarkArtifact:
    return BenchmarkArtifact.model_validate(
        orjson.loads(open("tests/fixtures/evaluation/benchmark.json", "rb").read())
    )


def _response(world_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": world_id,
        "response": {
            "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}], "role": "model"}}]
        },
    }


def _extraction(
    world_id: str, benchmark: BenchmarkArtifact, language: str
) -> PatientExtractionResult:
    world = next(item for item in benchmark.worlds if item.world_id == world_id)
    facts = []
    for fact in world.facts:
        span = world.fact_span_map[fact.fact_id][0]
        facts.append(
            PatientFactProposal(
                slot_id=fact.slot_id,
                value=fact.value,
                start=span.start,
                end=span.end,
                quote=span.quote,
                effective_date=world.evaluation_date,
                confidence=1.0,
            )
        )
    return PatientExtractionResult(
        facts=facts,
        retrieval_hypotheses=[],
        possible_conflicts=[],
        unparsed_spans=[],
        language=language,
    )


def test_fixed_seed_paraphrase_selection_and_batch_requests_are_exact() -> None:
    benchmark = _benchmark()
    selected = select_paraphrase_worlds(benchmark)
    requests, repeated = build_paraphrase_requests(
        benchmark,
        prompt_template=(
            "lang={target_language}\nfacts={structured_facts_json}\ntext={template_narrative}"
        ),
    )

    assert len(selected) == round(len(benchmark.worlds) * 0.30)
    assert repeated == selected
    assert [item.language for item in selected] == ["ko", "en", "ko"]
    assert {row["id"] for row in requests} == {item.world_id for item in selected}
    assert all(
        row["request"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
        for row in requests
    )
    assert all("temperature" not in row["request"]["generationConfig"] for row in requests)

    candidates = paraphrase_candidate_worlds(benchmark)
    alternate = candidates[len(selected) : len(selected) + 2]
    alternate_requests, repeated_alternate = build_paraphrase_requests(
        benchmark,
        prompt_template=(
            "lang={target_language}\nfacts={structured_facts_json}\ntext={template_narrative}"
        ),
        selected=alternate,
    )
    assert repeated_alternate == alternate
    assert {row["id"] for row in alternate_requests} == {item.world_id for item in alternate}


def test_extraction_batch_requests_use_frozen_medium_thinking_without_temperature() -> None:
    requests = build_extraction_requests(
        {"world-1": "Patient narrative."},
        patient_prompt_template="Extract from {patient_text}",
        response_schema=patient_extraction_batch_response_schema(),
    )

    generation_config = requests[0]["request"]["generationConfig"]
    assert generation_config["thinkingConfig"] == {"thinkingLevel": "MEDIUM"}
    assert generation_config["maxOutputTokens"] == 2000
    assert "temperature" not in generation_config
    schema_text = orjson.dumps(generation_config["responseJsonSchema"]).decode()
    assert "oneOf" not in schema_text
    assert '"type":"null"' not in schema_text
    typed_value = generation_config["responseJsonSchema"]["properties"]["facts"]["items"][
        "properties"
    ]["value"]
    assert set(typed_value["properties"]) >= {
        "kind",
        "value",
        "days",
        "lower",
        "upper",
        "reason",
    }
    assert typed_value["properties"]["value"] == {"type": "string"}
    assert typed_value["required"] == ["kind", "value"]


def test_paraphrase_is_applied_only_after_all_facts_are_recovered() -> None:
    benchmark = _benchmark()
    selected = select_paraphrase_worlds(benchmark)
    worlds = {item.world_id: item for item in benchmark.worlds}
    response_rows = [
        _response(item.world_id, {"narrative": worlds[item.world_id].narrative})
        for item in selected
    ]
    paraphrases = parse_paraphrase_responses(response_rows, selected)
    extractions = {
        item.world_id: _extraction(item.world_id, benchmark, item.language) for item in selected
    }

    updated = apply_validated_paraphrases(benchmark, paraphrases, extractions, selected)

    assert updated.counts["paraphrased_worlds"] == len(selected)
    assert sum(
        world.narrative_method == "FLASH_LITE_PARAPHRASE" for world in updated.worlds
    ) == len(selected)
    assert updated.acceptance_eligible is False

    first = selected[0]
    broken = extractions[first.world_id].model_copy(
        update={"facts": extractions[first.world_id].facts[:-1]}
    )
    with pytest.raises(ValueError, match="PARAPHRASE_FACT_RECOVERY_MISMATCH"):
        apply_validated_paraphrases(
            benchmark,
            paraphrases,
            {**extractions, first.world_id: broken},
            selected,
        )
