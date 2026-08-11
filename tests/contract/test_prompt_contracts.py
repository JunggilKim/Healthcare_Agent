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
