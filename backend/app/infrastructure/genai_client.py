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
        http_options=types.HttpOptions(api_version="v1"),
    )
