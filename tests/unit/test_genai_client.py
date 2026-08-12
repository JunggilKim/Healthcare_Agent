from __future__ import annotations

import pytest

from backend.app.infrastructure import genai_client
from backend.app.settings import Settings


def test_genai_client_uses_first_party_enterprise_v1_without_api_key(monkeypatch) -> None:
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(genai_client.genai, "Client", fake_client)
    settings = Settings(
        google_cloud_project="trial-opt-test",
        google_cloud_location="global",
    )
    client = genai_client.create_google_cloud_genai_client(settings)
    assert client is not None
    assert captured["enterprise"] is True
    assert captured["project"] == "trial-opt-test"
    assert captured["location"] == "global"
    assert captured["http_options"].api_version == "v1"
    assert captured["http_options"].timeout == 60_000
    assert captured["http_options"].retry_options.attempts == 1
    assert "api_key" not in captured


def test_genai_client_refuses_live_initialization_without_project() -> None:
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        genai_client.create_google_cloud_genai_client(Settings(google_cloud_project=""))
