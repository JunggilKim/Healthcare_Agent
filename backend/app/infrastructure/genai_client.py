from __future__ import annotations

from google import genai
from google.genai import types

from backend.app.settings import Settings


def create_google_cloud_genai_client(settings: Settings) -> genai.Client:
    if not settings.google_cloud_project:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required for live model calls")
    return genai.Client(
        enterprise=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        # Retry policy lives in StructuredGenerator, where reservations,
        # circuit-breaker state, and retry telemetry are kept consistent.
        # Disabling the SDK's nested five-attempt retry prevents one logical
        # attempt from blocking for roughly five request timeouts.
        http_options=types.HttpOptions(
            api_version="v1",
            timeout=60_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
