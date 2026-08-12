from __future__ import annotations

from types import SimpleNamespace

from scripts import validate_model_access


class _Models:
    def __init__(self) -> None:
        self.generated: list[str] = []
        self.configs = []

    def generate_content(self, *, model, contents, config):
        self.generated.append(model)
        self.configs.append(config)
        return SimpleNamespace(
            text="ACCESS_OK",
            usage_metadata=SimpleNamespace(
                prompt_token_count=4, candidates_token_count=2, total_token_count=6
            ),
        )

    def embed_content(self, *, model, contents, config):
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.0] * 768)], usage_metadata=None
        )


def test_run_probes_uses_every_frozen_model_once(monkeypatch) -> None:
    models = _Models()
    monkeypatch.setattr(
        validate_model_access,
        "create_google_cloud_genai_client",
        lambda settings: SimpleNamespace(models=models),
    )

    result = validate_model_access.run_probes(
        project="trial-opt-test",
        location="global",
        config_path=validate_model_access.REPOSITORY_ROOT / "config" / "models.yaml",
    )

    assert models.generated == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert [config.max_output_tokens for config in models.configs] == [256, 256]
    assert [config.thinking_config.thinking_level for config in models.configs] == [
        "MEDIUM",
        "LOW",
    ]
    assert result["models"] == [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-embedding-001",
    ]
    assert result["probes"][-1]["embedding_dimensions"] == 768
