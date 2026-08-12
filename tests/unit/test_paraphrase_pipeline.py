from __future__ import annotations

import json

import orjson
import pytest

from backend.app.domain.model_outputs import PatientExtractionResult, PatientFactProposal
from backend.app.evaluation.models import BenchmarkArtifact
from backend.app.evaluation.paraphrases import (
    apply_validated_paraphrases,
    build_paraphrase_requests,
    inline_json_schema_references,
    parse_paraphrase_responses,
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
        orjson.loads(open("data/eval/generated/benchmark.json", "rb").read())
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
    assert all(row["request"]["generationConfig"]["temperature"] == 0.2 for row in requests)


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
