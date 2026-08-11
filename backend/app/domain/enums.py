from __future__ import annotations

from enum import Enum


class EvidenceGrade(str, Enum):
    A_DIRECT = "A"
    B_DETERMINISTIC = "B"
    C_ONTOLOGY = "C"
    H_HYPOTHESIS = "H"


class CriterionVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONFLICT = "CONFLICT"


class TrialDecision(str, Enum):
    PRE_SCREEN_PASS = "PRE_SCREEN_PASS"
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INELIGIBLE = "INELIGIBLE"
    IRRELEVANT = "IRRELEVANT"


class SourceDirection(str, Enum):
    INCLUSION = "INCLUSION"
    EXCLUSION = "EXCLUSION"
    REGISTRY_FIELD = "REGISTRY_FIELD"


class AcquisitionAction(str, Enum):
    ASK_PATIENT = "ASK_PATIENT"
    REQUEST_VALUE = "REQUEST_VALUE"
    REQUEST_RECORD = "REQUEST_RECORD"
    CLINICIAN_REVIEW = "CLINICIAN_REVIEW"
    STOP_AND_REPORT = "STOP_AND_REPORT"
