from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from backend.app.agents.prompts import render_prompt
from backend.app.application.catalog import SlotCatalog, SlotDefinition
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import EvidenceGrade
from backend.app.domain.evidence import (
    FactConflict,
    PatientFact,
    PatientState,
    RetrievalHypothesis,
    SourceSpan,
)
from backend.app.domain.model_outputs import (
    PatientExtractionResult,
    PatientFactProposal,
    RetrievalHypothesisProposal,
    UnparsedSpan,
)
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    StringValue,
    UnknownValue,
)
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)


class PatientExtractionValidationError(ValueError):
    pass


def compact_patient_slot_catalog(slot_catalog: SlotCatalog) -> str:
    return canonical_json_bytes(
        [
            {
                "slot_id": slot.slot_id,
                "value_type": slot.value_type,
                "canonical_values": slot.canonical_values,
                "allowed_units": slot.allowed_units,
                "aliases": slot.aliases,
            }
            for slot in slot_catalog.slots
        ]
    ).decode()


@dataclass(frozen=True)
class MaterializedPatientExtraction:
    state: PatientState
    unparsed_spans: list[UnparsedSpan]


def _validate_span(text: str, start: int, end: int, quote: str) -> None:
    if not 0 <= start < end <= len(text) or text[start:end] != quote:
        raise PatientExtractionValidationError(
            "proposal source span does not match immutable input"
        )


def _validate_value(proposal: PatientFactProposal, slot: SlotDefinition) -> None:
    value = proposal.value
    if isinstance(value, UnknownValue):
        raise PatientExtractionValidationError("unknown values must be omitted from facts")
    if slot.value_type == "boolean" and not isinstance(value, BooleanValue):
        raise PatientExtractionValidationError("boolean slot requires BooleanValue")
    if slot.value_type == "number":
        if not isinstance(value, NumberValue):
            raise PatientExtractionValidationError("numeric slot requires NumberValue")
        if value.unit not in slot.allowed_units:
            raise PatientExtractionValidationError("numeric unit is not allowed for slot")
        if slot.allowed_range and not slot.allowed_range[0] <= value.value <= slot.allowed_range[1]:
            raise PatientExtractionValidationError("numeric value is outside slot range")
    if slot.value_type == "date" and not isinstance(value, DateValue):
        raise PatientExtractionValidationError("date slot requires DateValue")
    if slot.value_type == "duration" and not isinstance(value, DurationValue):
        raise PatientExtractionValidationError("duration slot requires DurationValue")
    if slot.value_type == "categorical":
        if not isinstance(value, CategoricalValue):
            raise PatientExtractionValidationError("categorical slot requires CategoricalValue")
        if value.value not in slot.canonical_values:
            raise PatientExtractionValidationError("categorical value is not canonical")
    if slot.value_type == "categorical_free_string" and not isinstance(
        value, (CategoricalValue, StringValue)
    ):
        raise PatientExtractionValidationError(
            "categorical free-string slot requires categorical or string value"
        )


def materialize_patient_extraction(
    *,
    patient_text: str,
    source_id: str,
    proposal: PatientExtractionResult,
    slot_catalog: SlotCatalog,
    asserted_at: datetime,
) -> MaterializedPatientExtraction:
    slots = slot_catalog.by_id()
    facts: list[PatientFact] = []
    for fact_index, fact_proposal in enumerate(proposal.facts):
        _validate_span(
            patient_text,
            fact_proposal.start,
            fact_proposal.end,
            fact_proposal.quote,
        )
        slot = slots.get(fact_proposal.slot_id)
        if slot is None:
            raise PatientExtractionValidationError(f"unknown slot: {fact_proposal.slot_id}")
        _validate_value(fact_proposal, slot)
        quote_hash = hashlib.sha256(fact_proposal.quote.encode()).hexdigest()
        facts.append(
            PatientFact(
                fact_id=(
                    "fact_"
                    + hashlib.sha256(
                        (
                            f"fact:{fact_index}:{fact_proposal.slot_id}:"
                            f"{fact_proposal.start}:{fact_proposal.end}:{quote_hash}"
                        ).encode()
                    ).hexdigest()[:24]
                ),
                slot_id=fact_proposal.slot_id,
                value=fact_proposal.value,
                grade=EvidenceGrade.A_DIRECT,
                source_spans=[
                    SourceSpan(
                        source_id=source_id,
                        start=fact_proposal.start,
                        end=fact_proposal.end,
                        quote=fact_proposal.quote,
                        sha256=quote_hash,
                        language=proposal.language,
                    )
                ],
                derived_from_fact_ids=[],
                transformation_id=None,
                asserted_at=asserted_at.astimezone(UTC),
                effective_date=fact_proposal.effective_date,
                admissible_for_hard_decision="A" in slot.hard_admissible_grades,
                confidence=fact_proposal.confidence,
            )
        )

    hypotheses: list[RetrievalHypothesis] = []
    for hypothesis_index, hypothesis in enumerate(proposal.retrieval_hypotheses):
        if not hypothesis.source_proposal_indexes or any(
            index < 0 or index >= len(facts) for index in hypothesis.source_proposal_indexes
        ):
            raise PatientExtractionValidationError("hypothesis source index is invalid")
        hypotheses.append(
            RetrievalHypothesis(
                hypothesis_id=(
                    "hyp_"
                    + hashlib.sha256(
                        (f"hypothesis:{hypothesis_index}:{hypothesis.normalized_concept}").encode()
                    ).hexdigest()[:24]
                ),
                concept=hypothesis.concept,
                normalized_concept=hypothesis.normalized_concept,
                source_fact_ids=[
                    facts[index].fact_id for index in hypothesis.source_proposal_indexes
                ],
                rationale_code=hypothesis.rationale_code,
                grade=EvidenceGrade.H_HYPOTHESIS,
                admissible_for_eligibility=False,
            )
        )

    conflicts: list[FactConflict] = []
    for conflict_index, conflict in enumerate(proposal.possible_conflicts):
        if any(index < 0 or index >= len(facts) for index in conflict.proposal_indexes):
            raise PatientExtractionValidationError("conflict source index is invalid")
        referenced = [facts[index] for index in conflict.proposal_indexes]
        if any(fact.slot_id != conflict.slot_id for fact in referenced):
            raise PatientExtractionValidationError("conflict facts must share the declared slot")
        conflicts.append(
            FactConflict(
                conflict_id=(
                    "conflict_"
                    + hashlib.sha256(
                        (
                            f"conflict:{conflict_index}:{conflict.slot_id}:"
                            + ":".join(item.fact_id for item in referenced)
                        ).encode()
                    ).hexdigest()[:24]
                ),
                slot_id=conflict.slot_id,
                fact_ids=[fact.fact_id for fact in referenced],
                conflict_type=conflict.conflict_type,
                status="OPEN",
            )
        )

    for span in proposal.unparsed_spans:
        _validate_span(patient_text, span.start, span.end, span.quote)
    return MaterializedPatientExtraction(
        state=PatientState(
            confirmed_facts=facts,
            retrieval_hypotheses=hypotheses,
            conflicts=conflicts,
        ),
        unparsed_spans=proposal.unparsed_spans,
    )


_ENGLISH_AGE = re.compile(r"\b(?P<age>\d{1,3})[- ]year[- ]old\b", re.IGNORECASE)
_KOREAN_AGE = re.compile(r"(?<!\d)(?:만\s*)?(?P<age>\d{1,3})세")
_SEX_TERMS = {
    "man": "male",
    "male": "male",
    "woman": "female",
    "female": "female",
    "남성": "male",
    "여성": "female",
}


def deterministic_surface_fallback(
    patient_text: str, *, language: Literal["ko", "en", "other"] = "other"
) -> PatientExtractionResult:
    """Extract only unambiguous demographics; unsupported medicine remains unparsed."""
    facts: list[PatientFactProposal] = []
    age_match = _ENGLISH_AGE.search(patient_text) or _KOREAN_AGE.search(patient_text)
    if age_match:
        facts.append(
            PatientFactProposal(
                slot_id="demographics.age",
                value=NumberValue(kind="number", value=age_match.group("age"), unit="year"),
                start=age_match.start(),
                end=age_match.end(),
                quote=age_match.group(0),
            )
        )
    for term, normalized in _SEX_TERMS.items():
        match = re.search(
            rf"(?<![\w가-힣]){re.escape(term)}(?![\w가-힣])", patient_text, re.IGNORECASE
        )
        if match:
            facts.append(
                PatientFactProposal(
                    slot_id="demographics.sex",
                    value=CategoricalValue(kind="categorical", value=normalized),
                    start=match.start(),
                    end=match.end(),
                    quote=match.group(0),
                )
            )
            break

    def add_surface_fact(
        pattern: str, slot_id: str, value: BooleanValue | CategoricalValue
    ) -> int | None:
        match = re.search(pattern, patient_text, re.IGNORECASE)
        if match is None:
            return None
        facts.append(
            PatientFactProposal(
                slot_id=slot_id,
                value=value,
                start=match.start(),
                end=match.end(),
                quote=match.group(0),
            )
        )
        return len(facts) - 1

    surface_indexes: dict[str, int] = {}
    surface_patterns: list[tuple[str, str, BooleanValue | CategoricalValue]] = [
        (
            r"chronic alcohol use",
            "alcohol.chronic_use",
            BooleanValue(kind="boolean", value=True),
        ),
        (
            r"severe epigastric pain",
            "symptom.epigastric_pain",
            BooleanValue(kind="boolean", value=True),
        ),
        (
            r"markedly elevated serum lipase",
            "lab.lipase_interpretation",
            CategoricalValue(kind="categorical", value="markedly_elevated"),
        ),
        (
            r"markedly elevated serum lipase and amylase",
            "lab.amylase_interpretation",
            CategoricalValue(kind="categorical", value="markedly_elevated"),
        ),
        (
            r"progressive dyspnea",
            "symptom.dyspnea",
            BooleanValue(kind="boolean", value=True),
        ),
        (r"dry cough", "symptom.dry_cough", BooleanValue(kind="boolean", value=True)),
        (
            r"honeycombing",
            "imaging.honeycombing",
            BooleanValue(kind="boolean", value=True),
        ),
        (
            r"gross hematuria",
            "symptom.gross_hematuria",
            BooleanValue(kind="boolean", value=True),
        ),
        (
            r"mass in the bladder wall",
            "imaging.bladder_wall_mass",
            BooleanValue(kind="boolean", value=True),
        ),
    ]
    for pattern, slot_id, value in surface_patterns:
        index = add_surface_fact(pattern, slot_id, value)
        if index is not None:
            surface_indexes[slot_id] = index

    hypotheses: list[RetrievalHypothesisProposal] = []
    if {
        "symptom.epigastric_pain",
        "lab.lipase_interpretation",
    } <= surface_indexes.keys():
        hypotheses.append(
            RetrievalHypothesisProposal(
                concept="acute pancreatitis",
                normalized_concept="acute pancreatitis",
                source_proposal_indexes=[
                    surface_indexes["symptom.epigastric_pain"],
                    surface_indexes["lab.lipase_interpretation"],
                ],
                rationale_code="SYMPTOM_LAB_RETRIEVAL_HYPOTHESIS_ONLY",
            )
        )
    if {
        "symptom.dyspnea",
        "imaging.honeycombing",
    } <= surface_indexes.keys():
        hypotheses.append(
            RetrievalHypothesisProposal(
                concept="interstitial lung disease",
                normalized_concept="interstitial lung disease",
                source_proposal_indexes=[
                    surface_indexes["symptom.dyspnea"],
                    surface_indexes["imaging.honeycombing"],
                ],
                rationale_code="SYMPTOM_IMAGING_RETRIEVAL_HYPOTHESIS_ONLY",
            )
        )
    if {
        "symptom.gross_hematuria",
        "imaging.bladder_wall_mass",
    } <= surface_indexes.keys():
        hypotheses.append(
            RetrievalHypothesisProposal(
                concept="bladder neoplasm",
                normalized_concept="bladder neoplasm",
                source_proposal_indexes=[
                    surface_indexes["symptom.gross_hematuria"],
                    surface_indexes["imaging.bladder_wall_mass"],
                ],
                rationale_code="SYMPTOM_IMAGING_RETRIEVAL_HYPOTHESIS_ONLY",
            )
        )
    return PatientExtractionResult(
        facts=facts,
        retrieval_hypotheses=hypotheses,
        possible_conflicts=[],
        unparsed_spans=[],
        language=language,
    )


class PatientEvidenceAgent:
    def __init__(self, generator: StructuredGenerator, slot_catalog: SlotCatalog) -> None:
        self.generator = generator
        self.slot_catalog = slot_catalog

    async def extract(
        self,
        *,
        patient_text: str,
        source_id: str,
        language_hint: Literal["ko", "en", "auto"],
        evaluation_date: date,
        asserted_at: datetime,
        pinned_fallback: PatientExtractionResult | None = None,
        session_id: str = "unscoped",
    ) -> tuple[MaterializedPatientExtraction, bool]:
        normalized_input = {
            "patient_text": patient_text,
            "language_hint": language_hint,
            "evaluation_date": evaluation_date.isoformat(),
            "slot_catalog_version": self.slot_catalog.version,
            "existing_facts": [],
            "task": "initial_extraction",
        }
        prompt = render_prompt(
            "patient_extraction_v1.md",
            patient_text=patient_text,
            slot_catalog=compact_patient_slot_catalog(self.slot_catalog),
        )
        degraded = False

        def fallback_proposal() -> PatientExtractionResult:
            if pinned_fallback is not None:
                return pinned_fallback
            language: Literal["ko", "en", "other"]
            if language_hint == "ko":
                language = "ko"
            elif language_hint == "en":
                language = "en"
            else:
                language = "other"
            return deterministic_surface_fallback(patient_text, language=language)

        try:
            proposal, _ = await self.generator.generate_primary_with_lite_fallback(
                primary_model_id="gemini-3.6-flash",
                lite_model_id="gemini-3.5-flash-lite",
                task_name="patient_extraction",
                prompt=prompt,
                prompt_version="1.1.0",
                output_schema_version="patient-extraction-v1",
                slot_catalog_version=self.slot_catalog.version,
                normalized_input=normalized_input,
                output_model=PatientExtractionResult,
                primary_thinking_level="MEDIUM",
                fallback_thinking_level="HIGH",
                primary_max_output_tokens=2000,
                fallback_max_output_tokens=2000,
                session_id=session_id,
            )
        except StructuredGenerationUnavailable:
            degraded = True
            proposal = fallback_proposal()
        try:
            materialized = materialize_patient_extraction(
                patient_text=patient_text,
                source_id=source_id,
                proposal=proposal,
                slot_catalog=self.slot_catalog,
                asserted_at=asserted_at,
            )
        except PatientExtractionValidationError:
            if degraded:
                raise
            degraded = True
            materialized = materialize_patient_extraction(
                patient_text=patient_text,
                source_id=source_id,
                proposal=fallback_proposal(),
                slot_catalog=self.slot_catalog,
                asserted_at=asserted_at,
            )
        return materialized, degraded
