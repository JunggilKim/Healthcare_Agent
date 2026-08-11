from __future__ import annotations

from backend.app.agents.prompts import render_prompt
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.evidence import PatientState
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)
from backend.app.retrieval.models import RetrievalQuery
from backend.app.retrieval.query_builder import build_deterministic_query


class RetrievalQueryAgent:
    def __init__(self, generator: StructuredGenerator) -> None:
        self.generator = generator

    async def generate(
        self,
        state: PatientState,
        slot_catalog_version: str,
        *,
        session_id: str = "unscoped",
    ) -> RetrievalQuery:
        payload = state.model_dump(mode="json")
        prompt = render_prompt(
            "retrieval_query_v1.md",
            patient_state=canonical_json_bytes(payload).decode(),
        )
        try:
            query, _ = await self.generator.generate(
                model_id="gemini-3.5-flash-lite",
                task_name="retrieval_query",
                prompt=prompt,
                prompt_version="1.0.0",
                output_schema_version="retrieval-query-v1",
                slot_catalog_version=slot_catalog_version,
                normalized_input=payload,
                output_model=RetrievalQuery,
                thinking_level="LOW",
                max_output_tokens=800,
                max_attempts=2,
                session_id=session_id,
            )
            return query
        except StructuredGenerationUnavailable:
            return build_deterministic_query(state.confirmed_facts, state.retrieval_hypotheses)
