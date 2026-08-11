from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.domain.trials import RawTrialRecord, TrialLocation

ALLOWED_STATUSES = frozenset({"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"})


class CtgovSchemaError(ValueError):
    pass


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Identification(_ApiModel):
    nctId: str
    briefTitle: str
    officialTitle: str | None = None


class _Status(_ApiModel):
    overallStatus: str
    startDateStruct: dict[str, Any] | None = None
    completionDateStruct: dict[str, Any] | None = None
    studyFirstPostDateStruct: dict[str, Any] | None = None
    lastUpdatePostDateStruct: dict[str, Any] | None = None


class _Design(_ApiModel):
    studyType: str
    phases: list[str] = []


class _ProtocolSection(_ApiModel):
    identificationModule: _Identification
    statusModule: _Status
    designModule: _Design
    sponsorCollaboratorsModule: dict[str, Any] | None = None
    conditionsModule: dict[str, Any] | None = None
    armsInterventionsModule: dict[str, Any] | None = None
    descriptionModule: dict[str, Any] | None = None
    eligibilityModule: dict[str, Any] | None = None
    contactsLocationsModule: dict[str, Any] | None = None


class _Study(_ApiModel):
    protocolSection: _ProtocolSection
    derivedSection: dict[str, Any] | None = None


class _StudyPage(_ApiModel):
    studies: list[_Study]
    nextPageToken: str | None = None
    totalCount: int | None = None


def validate_study_page(content: bytes) -> list[dict[str, Any]]:
    try:
        parsed = _StudyPage.model_validate(orjson.loads(content))
    except (orjson.JSONDecodeError, ValidationError) as error:
        raise CtgovSchemaError("invalid ClinicalTrials.gov studies response") from error
    return [study.model_dump(by_alias=True) for study in parsed.studies]


def validate_single_study(content: bytes) -> dict[str, Any]:
    try:
        parsed = _Study.model_validate(orjson.loads(content))
    except (orjson.JSONDecodeError, ValidationError) as error:
        raise CtgovSchemaError("invalid ClinicalTrials.gov study response") from error
    return parsed.model_dump(by_alias=True)


def _module(protocol: dict[str, Any], name: str) -> dict[str, Any]:
    value = protocol.get(name)
    return value if isinstance(value, dict) else {}


def _parse_date(value: object) -> date | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_study(
    study: dict[str, Any],
    *,
    api_version: str,
    retrieved_at: datetime,
    raw_bytes: bytes,
    raw_uri: str | None = None,
) -> RawTrialRecord:
    protocol = study["protocolSection"]
    identification = _module(protocol, "identificationModule")
    status = _module(protocol, "statusModule")
    design = _module(protocol, "designModule")
    conditions = _module(protocol, "conditionsModule")
    descriptions = _module(protocol, "descriptionModule")
    eligibility = _module(protocol, "eligibilityModule")
    contacts = _module(protocol, "contactsLocationsModule")
    interventions = _module(protocol, "armsInterventionsModule")
    derived = study.get("derivedSection") or {}
    misc = derived.get("miscInfoModule") or {}

    return RawTrialRecord(
        nct_id=str(identification["nctId"]),
        api_version=api_version,
        retrieved_at=retrieved_at.astimezone(UTC),
        source_json_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        version_holder=misc.get("versionHolder"),
        last_update_post_date=_parse_date(status.get("lastUpdatePostDateStruct")),
        overall_status=str(status["overallStatus"]),
        study_type=str(design["studyType"]),
        official_title=identification.get("officialTitle"),
        brief_title=str(identification["briefTitle"]),
        conditions=list(conditions.get("conditions") or []),
        keywords=list(conditions.get("keywords") or []),
        brief_summary=descriptions.get("briefSummary"),
        detailed_description=descriptions.get("detailedDescription"),
        eligibility_criteria=eligibility.get("eligibilityCriteria"),
        sex=eligibility.get("sex"),
        minimum_age=eligibility.get("minimumAge"),
        maximum_age=eligibility.get("maximumAge"),
        healthy_volunteers=eligibility.get("healthyVolunteers"),
        phases=list(design.get("phases") or []),
        intervention_names=[
            str(item["name"])
            for item in interventions.get("interventions") or []
            if isinstance(item, dict) and item.get("name")
        ],
        locations=[
            TrialLocation(
                facility=item.get("facility"),
                city=item.get("city"),
                state=item.get("state"),
                country=item.get("country"),
                status=item.get("status"),
            )
            for item in contacts.get("locations") or []
            if isinstance(item, dict)
        ],
        raw_gcs_uri=raw_uri,
    )


def is_interactive_candidate(trial: RawTrialRecord) -> bool:
    return trial.study_type == "INTERVENTIONAL" and trial.overall_status in ALLOWED_STATUSES
