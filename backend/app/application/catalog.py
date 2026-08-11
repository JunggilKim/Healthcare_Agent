from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.domain.canonical import load_yaml
from backend.app.settings import REPOSITORY_ROOT


class SlotDefinition(StrictModel):
    slot_id: str
    value_type: Literal["boolean", "number", "categorical", "categorical_free_string", "date"]
    canonical_values: list[str] = Field(default_factory=list)
    allowed_range: list[int] = Field(default_factory=list)
    allowed_units: list[str]
    aliases: list[str]
    hard_admissible_grades: list[Literal["A", "B"]]
    default_action: Literal["ASK_PATIENT", "REQUEST_VALUE", "REQUEST_RECORD", "CLINICIAN_REVIEW"]
    burden_class: Literal[
        "boolean_patient_known",
        "categorical_patient_known",
        "numeric_or_date",
        "request_record",
        "clinician_review",
    ]
    sensitivity_class: Literal["ordinary", "moderate", "high"]
    question_template_ko: str
    question_template_en: str


class SlotCatalog(StrictModel):
    version: str
    namespaces: list[str]
    slots: list[SlotDefinition]

    def by_id(self) -> dict[str, SlotDefinition]:
        result = {slot.slot_id: slot for slot in self.slots}
        if len(result) != len(self.slots):
            raise ValueError("slot IDs must be unique")
        return result


@lru_cache(maxsize=1)
def load_slot_catalog(path: Path | None = None) -> SlotCatalog:
    config_path = path or REPOSITORY_ROOT / "config" / "slots.yaml"
    return SlotCatalog.model_validate(load_yaml(config_path))
