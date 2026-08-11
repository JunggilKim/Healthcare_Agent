from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import orjson
from google import genai
from google.genai import types


class EmbeddingUnavailableError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    async def embed_query(self, text: str) -> np.ndarray: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]: ...


class DisabledEmbeddingProvider:
    async def embed_query(self, text: str) -> np.ndarray:
        raise EmbeddingUnavailableError("dense embedding disabled")

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        raise EmbeddingUnavailableError("dense embedding disabled")


class RecordedEmbeddingProvider:
    """Hash-keyed fixture adapter; no text is sent off-device."""

    def __init__(self, fixture_path: Path) -> None:
        payload = orjson.loads(fixture_path.read_bytes())
        self.model = str(payload["model"])
        self.dim = int(payload["dimension"])
        self._vectors = {
            str(key): np.asarray(value, dtype=np.float64)
            for key, value in payload["vectors"].items()
        }

    def _lookup(self, task_type: str, text: str) -> np.ndarray:
        key = hashlib.sha256(f"{task_type}\0{text}".encode()).hexdigest()
        vector = self._vectors.get(key)
        if vector is None or vector.shape != (self.dim,):
            raise EmbeddingUnavailableError(f"no valid recorded embedding for {task_type}")
        return vector.copy()

    async def embed_query(self, text: str) -> np.ndarray:
        return self._lookup("RETRIEVAL_QUERY", text)

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [self._lookup("RETRIEVAL_DOCUMENT", text) for text in texts]


class GeminiEmbeddingProvider:
    def __init__(
        self,
        client: genai.Client,
        *,
        model: str = "gemini-embedding-001",
        dimension: int = 768,
        concurrency: int = 5,
    ) -> None:
        self.client = client
        self.model = model
        self.dimension = dimension
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _embed_one(self, text: str, task_type: str) -> np.ndarray:
        async with self._semaphore:
            try:
                response = await self.client.aio.models.embed_content(
                    model=self.model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.dimension,
                    ),
                )
            except Exception as error:
                raise EmbeddingUnavailableError("Gemini embedding request failed") from error
        embeddings = response.embeddings or []
        if len(embeddings) != 1 or embeddings[0].values is None:
            raise EmbeddingUnavailableError("Gemini returned no embedding")
        vector = np.asarray(embeddings[0].values, dtype=np.float64)
        if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
            raise EmbeddingUnavailableError("Gemini returned an invalid embedding")
        return vector

    async def embed_query(self, text: str) -> np.ndarray:
        return await self._embed_one(text, "RETRIEVAL_QUERY")

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        if len(texts) > 20:
            raise ValueError("at most 20 uncached document embeddings are allowed")
        return await asyncio.gather(
            *(self._embed_one(text, "RETRIEVAL_DOCUMENT") for text in texts)
        )
