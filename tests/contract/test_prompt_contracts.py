from __future__ import annotations

import hashlib
from pathlib import Path

import orjson

PROMPT_ROOT = Path("prompts")


def test_all_prompt_contracts_have_security_schema_and_semantic_headers() -> None:
    expected_placeholders = {
        "patient_extraction_v1.md": ["{patient_text}"],
        "retrieval_query_v1.md": ["{patient_state}"],
        "protocol_compiler_v1.md": [
            "{slot_catalog}",
            "{operator_definitions}",
            "{trial_payload}",
        ],
        "protocol_reviewer_v1.md": ["{review_payload}"],
        "answer_interpreter_v1.md": ["{answer_payload}"],
        "question_renderer_v1.md": ["{render_payload}"],
        "report_renderer_v1.md": ["{report_payload}"],
    }
    for filename, placeholders in expected_placeholders.items():
        text = (PROMPT_ROOT / filename).read_text(encoding="utf-8")
        first_lines = text.splitlines()[:5]
        assert first_lines[0].startswith("prompt_id:")
        assert first_lines[1].startswith("version:")
        assert first_lines[2].startswith("model:")
        assert first_lines[3].startswith("task:")
        assert first_lines[4].startswith("output_schema_version:")
        assert "untrusted" in text.lower()
        assert "json" in text.lower() and "schema" in text.lower()
        assert "diagnos" in text.lower()
        assert "assumption" in text.lower() or "uncertain" in text.lower()
        assert all(placeholder in text for placeholder in placeholders)
        assert len(hashlib.sha256(text.encode()).hexdigest()) == 64


def test_exported_logic_schemas_forbid_extra_fields() -> None:
    for path in Path("schemas").glob("*.schema.json"):
        schema = orjson.loads(path.read_bytes())
        assert schema["additionalProperties"] is False


def test_protocol_compiler_prompt_requires_compact_json_and_opaque_metadata() -> None:
    text = (PROMPT_ROOT / "protocol_compiler_v1.md").read_text(encoding="utf-8")
    assert "version: 1.0.3" in text
    assert "compact schema-valid JSON" in text
    assert "zero-based Unicode code-point offsets" in text
    assert "eligibility_criteria[start:end]" in text
    assert "label nodes exactly n0, n1" in text
    assert "metadata.reason_code" in text
    assert "metadata.residual_source_sha256" in text
    assert "value=null" in text
    assert "values=[]" in text


def test_protocol_reviewer_prompt_requires_compact_blocking_issues() -> None:
    text = (PROMPT_ROOT / "protocol_reviewer_v1.md").read_text(encoding="utf-8")
    assert "version: 1.0.2" in text
    assert "compact" in text and "schema-valid JSON" in text
    assert "every" in text and "distinct BLOCKING issue" in text
