from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.evidence import SourceSpan
from backend.app.domain.model_outputs import PatientExtractionResult, PatientFactProposal
from backend.app.evaluation.models import BenchmarkArtifact, PatientWorld, WorldFact

PARAPHRASE_MODEL_ID = "gemini-3.5-flash-lite"
EXTRACTION_MODEL_ID = "gemini-3.6-flash"
PARAPHRASE_PROMPT_VERSION = "synthetic-paraphrase-v1"


@dataclass(frozen=True, slots=True)
class SelectedWorld:
    world_id: str
    language: Literal["ko", "en"]


def patient_extraction_batch_response_schema() -> dict[str, Any]:
    """Return a Vertex Batch-compatible schema for patient extraction.

    Vertex Batch currently rejects the discriminated `oneOf` emitted by
    Pydantic for ``TypedValue`` after converting absent schema fields to JSON
    nulls. The value object is therefore constrained by its discriminator here
    and validated against ``PatientExtractionResult`` after generation.
    """

    typed_value = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "boolean",
                    "number",
                    "string",
                    "categorical",
                    "date",
                    "duration",
                    "range",
                    "unknown",
                ],
            },
            # These optional fields are intentionally flattened instead of a
            # discriminated oneOf. Scalar unions avoid Vertex's invalid null
            # property expansion. Decimal/date/text values use their canonical
            # string representation while booleans remain JSON booleans; strict
            # post-generation Pydantic validation remains authoritative.
            "value": {
                "anyOf": [
                    {"type": "boolean"},
                    {"type": "string"},
                ]
            },
            "unit": {"type": "string"},
            "normalized": {"type": "string"},
            "system": {"type": "string"},
            "precision": {"type": "string", "enum": ["DAY", "MONTH", "YEAR"]},
            "days": {"type": "integer"},
            "lower": {"type": "string"},
            "upper": {"type": "string"},
            "lower_inclusive": {"type": "boolean"},
            "upper_inclusive": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["kind"],
    }
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot_id": {"type": "string"},
                        "value": typed_value,
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                        "quote": {"type": "string", "minLength": 1},
                        "effective_date": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["slot_id", "value", "start", "end", "quote"],
                },
            },
            "retrieval_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "concept": {"type": "string"},
                        "normalized_concept": {"type": "string"},
                        "source_proposal_indexes": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "rationale_code": {"type": "string"},
                    },
                    "required": [
                        "concept",
                        "normalized_concept",
                        "source_proposal_indexes",
                        "rationale_code",
                    ],
                },
            },
            "possible_conflicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot_id": {"type": "string"},
                        "proposal_indexes": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                        },
                        "conflict_type": {
                            "type": "string",
                            "enum": [
                                "VALUE_MISMATCH",
                                "TEMPORAL_OVERLAP",
                                "NEGATION_MISMATCH",
                                "UNIT_INCOMPATIBLE",
                                "SOURCE_AMBIGUITY",
                            ],
                        },
                    },
                    "required": ["slot_id", "proposal_indexes", "conflict_type"],
                },
            },
            "unparsed_spans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                        "quote": {"type": "string", "minLength": 1},
                        "reason_code": {"type": "string"},
                    },
                    "required": ["start", "end", "quote", "reason_code"],
                },
            },
            "language": {"type": "string", "enum": ["ko", "en", "other"]},
        },
        "required": [
            "facts",
            "retrieval_hypotheses",
            "possible_conflicts",
            "unparsed_spans",
            "language",
        ],
    }


def inline_json_schema_references(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local Pydantic refs so Vertex batch JSONL has no `$` field names."""

    result = deepcopy(schema)
    definitions = result.pop("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("JSON_SCHEMA_DEFINITIONS_INVALID")

    def visit(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [visit(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise ValueError("JSON_SCHEMA_REFERENCE_UNSUPPORTED")
            name = reference.removeprefix("#/$defs/")
            if name in stack or name not in definitions:
                raise ValueError("JSON_SCHEMA_REFERENCE_INVALID")
            target = definitions[name]
            if not isinstance(target, dict):
                raise ValueError("JSON_SCHEMA_DEFINITION_INVALID")
            merged = {
                **deepcopy(target),
                **{key: item for key, item in value.items() if key != "$ref"},
            }
            return visit(merged, (*stack, name))
        return {key: visit(item, stack) for key, item in value.items()}

    inlined = visit(result)
    if not isinstance(inlined, dict):
        raise ValueError("JSON_SCHEMA_ROOT_INVALID")
    return inlined


def _selection_key(seed: int, world_id: str) -> tuple[str, str]:
    return hashlib.sha256(f"{seed}:paraphrase:{world_id}".encode()).hexdigest(), world_id


def select_paraphrase_worlds(benchmark: BenchmarkArtifact) -> list[SelectedWorld]:
    count = min(120, round(len(benchmark.worlds) * 0.30))
    return paraphrase_candidate_worlds(benchmark)[:count]


def paraphrase_candidate_worlds(benchmark: BenchmarkArtifact) -> list[SelectedWorld]:
    ordered = sorted(
        benchmark.worlds,
        key=lambda world: _selection_key(benchmark.seed, world.world_id),
    )
    return [
        SelectedWorld(world_id=world.world_id, language="ko" if index % 2 == 0 else "en")
        for index, world in enumerate(ordered)
    ]


def build_paraphrase_requests(
    benchmark: BenchmarkArtifact,
    *,
    prompt_template: str,
    selected: list[SelectedWorld] | None = None,
) -> tuple[list[dict[str, Any]], list[SelectedWorld]]:
    worlds = {world.world_id: world for world in benchmark.worlds}
    selected = selected or select_paraphrase_worlds(benchmark)
    requests: list[dict[str, Any]] = []
    response_schema = {
        "type": "OBJECT",
        "properties": {"narrative": {"type": "STRING"}},
        "required": ["narrative"],
    }
    for item in selected:
        world = worlds[item.world_id]
        facts = [fact.model_dump(mode="json") for fact in world.facts]
        prompt = (
            prompt_template.replace(
                "{target_language}", "Korean" if item.language == "ko" else "English"
            )
            .replace("{structured_facts_json}", canonical_json_bytes(facts).decode())
            .replace("{template_narrative}", world.template_narrative)
        )
        requests.append(
            {
                "id": item.world_id,
                "request": {
                    "contents": [
                        {"role": "user", "parts": [{"text": prompt}]},
                    ],
                    "generationConfig": {
                        "maxOutputTokens": 768,
                        "thinkingConfig": {"thinkingLevel": "LOW"},
                        "responseMimeType": "application/json",
                        "responseSchema": response_schema,
                    },
                },
            }
        )
    return requests, selected


def _response_text(row: dict[str, Any]) -> str:
    if row.get("error"):
        raise ValueError(f"BATCH_RESPONSE_ERROR:{row.get('id')}:{row['error']}")
    response = row.get("response")
    if not isinstance(response, dict):
        raise ValueError("BATCH_RESPONSE_PAYLOAD_MISSING")
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError("BATCH_RESPONSE_CANDIDATE_INVALID")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        raise ValueError("BATCH_RESPONSE_TEXT_MISSING")
    text = parts[0].get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("BATCH_RESPONSE_TEXT_MISSING")
    return text


def parse_paraphrase_responses(
    response_rows: list[dict[str, Any]],
    selected: list[SelectedWorld],
) -> dict[str, str]:
    expected = {item.world_id for item in selected}
    responses: dict[str, str] = {}
    for row in response_rows:
        world_id = row.get("id")
        if not isinstance(world_id, str) or world_id not in expected or world_id in responses:
            raise ValueError("PARAPHRASE_RESPONSE_ID_INVALID")
        payload = json.loads(_response_text(row))
        if set(payload) != {"narrative"} or not isinstance(payload["narrative"], str):
            raise ValueError(f"PARAPHRASE_RESPONSE_SCHEMA_INVALID:{world_id}")
        narrative = payload["narrative"].strip()
        if not narrative:
            raise ValueError(f"PARAPHRASE_RESPONSE_EMPTY:{world_id}")
        responses[world_id] = narrative
    if set(responses) != expected:
        raise ValueError("PARAPHRASE_RESPONSE_SET_INCOMPLETE")
    return responses


def build_extraction_requests(
    paraphrases: dict[str, str],
    *,
    patient_prompt_template: str,
    response_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    response_schema = inline_json_schema_references(response_schema)
    return [
        {
            "id": world_id,
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": patient_prompt_template.replace("{patient_text}", narrative)}
                        ],
                    }
                ],
                "generationConfig": {
                    "maxOutputTokens": 2000,
                    "thinkingConfig": {"thinkingLevel": "MEDIUM"},
                    "responseMimeType": "application/json",
                    "responseJsonSchema": response_schema,
                },
            },
        }
        for world_id, narrative in sorted(paraphrases.items())
    ]


def parse_extraction_responses(
    response_rows: list[dict[str, Any]], expected_ids: set[str]
) -> dict[str, PatientExtractionResult]:
    responses: dict[str, PatientExtractionResult] = {}
    for row in response_rows:
        world_id = row.get("id")
        if not isinstance(world_id, str) or world_id not in expected_ids or world_id in responses:
            raise ValueError("EXTRACTION_RESPONSE_ID_INVALID")
        responses[world_id] = PatientExtractionResult.model_validate_json(_response_text(row))
    if set(responses) != expected_ids:
        raise ValueError("EXTRACTION_RESPONSE_SET_INCOMPLETE")
    return responses


def _value_key(fact: WorldFact) -> bytes:
    return canonical_json_bytes(
        {"slot_id": fact.slot_id, "value": fact.value.model_dump(mode="json")}
    )


def validate_paraphrase_spans(
    world: PatientWorld,
    narrative: str,
    extraction: PatientExtractionResult,
) -> dict[str, list[SourceSpan]]:
    expected: dict[bytes, list[WorldFact]] = {}
    for fact in world.facts:
        expected.setdefault(_value_key(fact), []).append(fact)
    extracted: dict[bytes, list[PatientFactProposal]] = {}
    for proposal in extraction.facts:
        key = canonical_json_bytes(
            {"slot_id": proposal.slot_id, "value": proposal.value.model_dump(mode="json")}
        )
        extracted.setdefault(key, []).append(proposal)
    if {key: len(value) for key, value in expected.items()} != {
        key: len(value) for key, value in extracted.items()
    }:
        raise ValueError(f"PARAPHRASE_FACT_RECOVERY_MISMATCH:{world.world_id}")
    spans: dict[str, list[SourceSpan]] = {}
    for key, facts in expected.items():
        proposals = extracted[key]
        for fact, proposal_obj in zip(
            sorted(facts, key=lambda item: item.fact_id), proposals, strict=True
        ):
            proposal = proposal_obj
            if (
                proposal.end > len(narrative)
                or narrative[proposal.start : proposal.end] != proposal.quote
            ):
                raise ValueError(f"PARAPHRASE_EXTRACTION_SPAN_INVALID:{world.world_id}")
            spans[fact.fact_id] = [
                SourceSpan(
                    source_id=f"benchmark:{world.world_id}:paraphrase",
                    start=proposal.start,
                    end=proposal.end,
                    quote=proposal.quote,
                    sha256=hashlib.sha256(proposal.quote.encode()).hexdigest(),
                    language=extraction.language,
                )
            ]
    return spans


def apply_validated_paraphrases(
    benchmark: BenchmarkArtifact,
    paraphrases: dict[str, str],
    extractions: dict[str, PatientExtractionResult],
    selected: list[SelectedWorld],
) -> BenchmarkArtifact:
    selection = {item.world_id: item for item in selected}
    if set(paraphrases) != set(extractions) or set(paraphrases) != set(selection):
        raise ValueError("PARAPHRASE_APPLY_SET_MISMATCH")
    updated: list[PatientWorld] = []
    for world in benchmark.worlds:
        item = selection.get(world.world_id)
        if item is None:
            updated.append(world)
            continue
        extraction = extractions[world.world_id]
        if extraction.language != item.language:
            raise ValueError(f"PARAPHRASE_LANGUAGE_MISMATCH:{world.world_id}")
        narrative = paraphrases[world.world_id]
        spans = validate_paraphrase_spans(world, narrative, extraction)
        artifact_hash = hashlib.sha256(
            canonical_json_bytes(
                {
                    "narrative": narrative,
                    "extraction": extraction.model_dump(mode="json"),
                }
            )
        ).hexdigest()
        updated.append(
            world.model_copy(
                update={
                    "narrative": narrative,
                    "narrative_language": item.language,
                    "narrative_method": "FLASH_LITE_PARAPHRASE",
                    "fact_span_map": spans,
                    "paraphrase_model_id": PARAPHRASE_MODEL_ID,
                    "paraphrase_prompt_version": PARAPHRASE_PROMPT_VERSION,
                    "paraphrase_artifact_hash": artifact_hash,
                }
            )
        )
    target_count = min(120, round(len(updated) * 0.30))
    paraphrased = sum(world.narrative_method == "FLASH_LITE_PARAPHRASE" for world in updated)
    blocking = [
        reason
        for reason in benchmark.blocking_reasons
        if "paraphrase validation is pending" not in reason
    ]
    if paraphrased != target_count:
        blocking.append(
            f"Validated paraphrase count is {paraphrased}; exact target is {target_count}."
        )
    counts = {**benchmark.counts, "paraphrased_worlds": paraphrased}
    return BenchmarkArtifact.model_validate(
        {
            **benchmark.model_dump(mode="json"),
            "worlds": [world.model_dump(mode="json") for world in updated],
            "counts": counts,
            "blocking_reasons": blocking,
            "acceptance_eligible": not blocking,
        }
    )
