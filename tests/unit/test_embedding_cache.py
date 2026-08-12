from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from google import genai

from backend.app.retrieval.embeddings import GeminiEmbeddingProvider


class _EmbeddingModels:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def embed_content(self, **_kwargs: object) -> object:
        self.calls += 1
        if self.fail:
            raise AssertionError("valid embedding cache should prevent a provider call")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0, 0.0])])


class _EmbeddingClient:
    def __init__(self, models: _EmbeddingModels) -> None:
        self.aio = SimpleNamespace(models=models)


@pytest.mark.asyncio
async def test_embedding_cache_is_hash_keyed_npz_with_metadata(tmp_path: Path) -> None:
    first_models = _EmbeddingModels()
    first = GeminiEmbeddingProvider(
        cast(genai.Client, _EmbeddingClient(first_models)), dimension=3, cache_root=tmp_path
    )
    original = await first.embed_query("public synthetic query")

    assert first_models.calls == 1
    assert np.allclose(original, [0.6, 0.8, 0.0])
    assert len(list(tmp_path.glob("*.npz"))) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
    with np.load(next(tmp_path.glob("*.npz")), allow_pickle=False) as archive:
        assert archive["vector"].dtype == np.float32

    cached_models = _EmbeddingModels(fail=True)
    cached = GeminiEmbeddingProvider(
        cast(genai.Client, _EmbeddingClient(cached_models)), dimension=3, cache_root=tmp_path
    )
    replayed = await cached.embed_query("public synthetic query")

    assert cached_models.calls == 0
    assert np.allclose(replayed, original, atol=1e-7)
