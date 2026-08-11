from __future__ import annotations

from backend.app.retrieval.tokenizer import RegexMedicalTokenizer


def test_fixed_medical_tokenizer_golden_cases() -> None:
    tokenizer = RegexMedicalTokenizer()
    assert tokenizer.tokenize("방광암 환자") == ["방광암", "환자"]
    assert tokenizer.tokenize("HER-2-positive non-small-cell") == [
        "her-2-positive",
        "non-small-cell",
    ]
    assert tokenizer.tokenize("eGFR 30.5 mL/min ≥ 30%") == [
        "e",
        "gfr",
        "30.5",
        "m",
        "l/min",
        "≥",
        "30",
        "%",
    ]
    assert tokenizer.tokenize("creatinineClearance < 45") == [
        "creatinine",
        "clearance",
        "<",
        "45",
    ]
