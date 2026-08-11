from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from backend.app.agents.patient_evidence import (
    MaterializedPatientExtraction,
    PatientExtractionValidationError,
    materialize_patient_extraction,
)
from backend.app.application.catalog import SlotCatalog, SlotDefinition
from backend.app.domain.model_outputs import PatientExtractionResult, PatientFactProposal
from backend.app.domain.questions import QuestionCandidate
from backend.app.domain.rendering import AnswerInterpretationProposal
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    StringValue,
    TypedValue,
)

_TYPED_VALUE_ADAPTER: TypeAdapter[TypedValue] = TypeAdapter(TypedValue)

_DECLINED = (
    "decline",
    "prefer not",
    "no test has been performed",
    "not available",
    "검사하지 않았",
    "확인할 수 없",
    "제공하지 않",
)
_UNKNOWN = ("unknown", "don't know", "do not know", "모르", "알 수 없")


@dataclass(frozen=True)
class InterpretedAnswer:
    materialized: MaterializedPatientExtraction | None
    unknown: bool
    declined: bool
    source: Literal["MODEL_VALIDATED", "DETERMINISTIC_FALLBACK"]
    rejection_code: str | None = None


def proposal_from_structured_answer(
    *,
    candidate: QuestionCandidate,
    structured_value: dict[str, object],
    slot_catalog: SlotCatalog,
) -> tuple[str, AnswerInterpretationProposal]:
    """Validate an API typed value and turn it into a span-verifiable answer proposal."""

    try:
        typed_value = _TYPED_VALUE_ADAPTER.validate_python(structured_value)
    except ValidationError as error:
        raise ValueError("STRUCTURED_VALUE_INVALID") from error
    allowed_types: dict[str, tuple[type[TypedValue], ...]] = {
        "boolean": (BooleanValue,),
        "number": (NumberValue,),
        "categorical": (CategoricalValue,),
        "categorical_free_string": (CategoricalValue, StringValue),
        "date": (DateValue,),
        "duration": (DurationValue,),
    }
    if not isinstance(typed_value, allowed_types[candidate.answer_type]):
        raise ValueError("STRUCTURED_VALUE_TYPE_MISMATCH")
    slot = slot_catalog.by_id()[candidate.slot_id]
    if slot.value_type == "categorical" and isinstance(typed_value, CategoricalValue):
        if slot.canonical_values and typed_value.value not in slot.canonical_values:
            raise ValueError("STRUCTURED_VALUE_NOT_IN_SLOT_CATALOG")
    if isinstance(typed_value, NumberValue) and slot.allowed_units:
        if typed_value.unit not in slot.allowed_units:
            raise ValueError("STRUCTURED_VALUE_UNIT_NOT_ALLOWED")
    answer_text = typed_value.model_dump_json()
    return (
        answer_text,
        AnswerInterpretationProposal(
            facts=[
                PatientFactProposal(
                    slot_id=candidate.slot_id,
                    value=typed_value,
                    start=0,
                    end=len(answer_text),
                    quote=answer_text,
                    confidence=1.0,
                )
            ]
        ),
    )


def _find(text: str, pattern: str) -> re.Match[str] | None:
    return re.search(pattern, text, re.IGNORECASE)


def _proposal_for_slot(text: str, slot: SlotDefinition) -> PatientFactProposal | None:
    match: re.Match[str] | None
    value: TypedValue
    if slot.slot_id == "pathology.histology":
        match = _find(text, r"(?:high[- ]grade\s+)?urothelial carcinoma")
        if match:
            value = CategoricalValue(
                kind="categorical",
                value="urothelial_carcinoma",
                system="trial-opt-canonical-v1",
            )
        else:
            return None
    elif slot.slot_id == "pathology.muscle_invasion":
        negative = _find(text, r"(?:non[- ]muscle[- ]invasive|no muscle invasion)")
        positive = _find(
            text,
            r"(?:muscle[- ]invasive|muscle invasion (?:is )?(?:present|confirmed))",
        )
        match = negative or positive
        if match is None:
            return None
        value = BooleanValue(kind="boolean", value=negative is None)
    elif slot.value_type == "boolean":
        negative = _find(text, r"\b(?:no|false|absent)\b|(?:없습니다|아닙니다|없음)")
        positive = _find(text, r"\b(?:yes|true|present)\b|(?:있습니다|맞습니다|있음)")
        match = negative or positive
        if match is None:
            return None
        value = BooleanValue(kind="boolean", value=negative is None)
    elif slot.value_type == "number":
        unit_pattern = "|".join(re.escape(unit) for unit in slot.allowed_units)
        pattern = rf"(?P<number>-?\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})"
        match = _find(text, pattern) if unit_pattern else None
        if match is None:
            return None
        try:
            number = Decimal(match.group("number"))
        except InvalidOperation:
            return None
        value = NumberValue(kind="number", value=number, unit=match.group("unit"))
    elif slot.value_type == "date":
        match = _find(text, r"\b\d{4}-\d{2}-\d{2}\b")
        if match is None:
            return None
        try:
            parsed = date.fromisoformat(match.group(0))
        except ValueError:
            return None
        value = DateValue(kind="date", value=parsed, precision="DAY")
    elif slot.value_type == "duration":
        match = _find(text, r"(?P<days>\d+)\s*(?:days?|일)")
        if match is None:
            return None
        value = DurationValue(kind="duration", days=int(match.group("days")))
    elif slot.value_type == "categorical":
        matched = next(
            (
                (canonical, found)
                for canonical in slot.canonical_values
                if (found := _find(text, rf"\b{re.escape(canonical)}\b")) is not None
            ),
            None,
        )
        match = matched[1] if matched else None
        if match is None:
            return None
        assert matched is not None
        value = CategoricalValue(kind="categorical", value=matched[0])
    else:
        stripped = text.strip()
        if not stripped:
            return None
        start = text.index(stripped)
        match = re.match(re.escape(stripped), text[start:])
        assert match is not None
        value = StringValue(kind="string", value=stripped, normalized=stripped.casefold())
        return PatientFactProposal(
            slot_id=slot.slot_id,
            value=value,
            start=start,
            end=start + len(stripped),
            quote=stripped,
        )
    return PatientFactProposal(
        slot_id=slot.slot_id,
        value=value,
        start=match.start(),
        end=match.end(),
        quote=match.group(0),
    )


def _materialize(
    *,
    text: str,
    source_id: str,
    proposal: PatientExtractionResult,
    slot_catalog: SlotCatalog,
    asserted_at: datetime,
) -> MaterializedPatientExtraction:
    return materialize_patient_extraction(
        patient_text=text,
        source_id=source_id,
        proposal=proposal,
        slot_catalog=slot_catalog,
        asserted_at=asserted_at,
    )


def interpret_answer(
    *,
    candidate: QuestionCandidate,
    answer_text: str,
    source_id: str,
    slot_catalog: SlotCatalog,
    asserted_at: datetime,
    proposal: AnswerInterpretationProposal | None = None,
) -> InterpretedAnswer:
    slot = slot_catalog.by_id()[candidate.slot_id]
    if proposal is not None:
        if (proposal.unknown and proposal.declined) or (
            proposal.facts and (proposal.unknown or proposal.declined)
        ):
            proposal = None
        elif any(fact.slot_id != candidate.slot_id for fact in proposal.facts):
            proposal = None
        else:
            try:
                materialized = _materialize(
                    text=answer_text,
                    source_id=source_id,
                    proposal=PatientExtractionResult(
                        facts=proposal.facts,
                        possible_conflicts=proposal.conflicts,
                        language="ko" if re.search(r"[가-힣]", answer_text) else "en",
                    ),
                    slot_catalog=slot_catalog,
                    asserted_at=asserted_at,
                )
                if materialized.state.confirmed_facts or proposal.unknown or proposal.declined:
                    return InterpretedAnswer(
                        materialized=materialized,
                        unknown=proposal.unknown,
                        declined=proposal.declined,
                        source="MODEL_VALIDATED",
                    )
            except PatientExtractionValidationError:
                pass

    normalized = answer_text.strip().casefold()
    declined = any(phrase in normalized for phrase in _DECLINED)
    unknown = not declined and (not normalized or any(phrase in normalized for phrase in _UNKNOWN))
    deterministic = _proposal_for_slot(answer_text, slot) if not (declined or unknown) else None
    if deterministic is None:
        return InterpretedAnswer(
            materialized=None,
            unknown=not declined,
            declined=declined,
            source="DETERMINISTIC_FALLBACK",
            rejection_code=(
                "NO_TYPE_SAFE_SELECTED_SLOT_VALUE" if not (unknown or declined) else None
            ),
        )
    materialized = _materialize(
        text=answer_text,
        source_id=source_id,
        proposal=PatientExtractionResult(
            facts=[deterministic],
            language="ko" if re.search(r"[가-힣]", answer_text) else "en",
        ),
        slot_catalog=slot_catalog,
        asserted_at=asserted_at,
    )
    return InterpretedAnswer(
        materialized=materialized,
        unknown=False,
        declined=False,
        source="DETERMINISTIC_FALLBACK",
    )
