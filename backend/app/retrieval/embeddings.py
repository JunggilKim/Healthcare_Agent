from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import numpy as np
import orjson
from google import genai
from google.genai import types

from backend.app.domain.canonical import canonical_json_bytes


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
        norm = float(np.linalg.norm(vector))
        if norm == 0 or not np.isfinite(vector).all():
            raise EmbeddingUnavailableError("recorded embedding is invalid")
        return vector.copy() / norm

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
        cache_root: Path | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.dimension = dimension
        self._semaphore = asyncio.Semaphore(concurrency)
        self.cache_root = cache_root

    def _cache_paths(self, text: str, task_type: str) -> tuple[str, Path, Path] | None:
        if self.cache_root is None:
            return None
        text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        key = hashlib.sha256(
            f"{self.model}\0{self.dimension}\0{task_type}\0{text_sha256}".encode()
        ).hexdigest()
        return (
            text_sha256,
            self.cache_root / f"{key}.npz",
            self.cache_root / f"{key}.json",
        )

    def _load_cached(self, text: str, task_type: str) -> np.ndarray | None:
        paths = self._cache_paths(text, task_type)
        if paths is None:
            return None
        text_sha256, vector_path, metadata_path = paths
        if not vector_path.is_file() or not metadata_path.is_file():
            return None
        try:
            metadata = orjson.loads(metadata_path.read_bytes())
            expected_metadata = {
                "dimension": self.dimension,
                "model": self.model,
                "task_type": task_type,
                "text_sha256": text_sha256,
            }
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                return None
            vector_bytes = vector_path.read_bytes()
            if metadata.get("vector_sha256") != hashlib.sha256(vector_bytes).hexdigest():
                return None
            with np.load(vector_path, allow_pickle=False) as archive:
                vector = np.asarray(archive["vector"], dtype=np.float64)
        except (OSError, KeyError, ValueError, orjson.JSONDecodeError):
            return None
        if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
            return None
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return None
        return vector / norm

    def _store_cached(self, text: str, task_type: str, vector: np.ndarray) -> None:
        paths = self._cache_paths(text, task_type)
        if paths is None:
            return
        text_sha256, vector_path, metadata_path = paths
        vector_path.parent.mkdir(parents=True, exist_ok=True)
        nonce = uuid4().hex
        vector_temporary = vector_path.with_name(f".{vector_path.name}.{nonce}.tmp")
        metadata_temporary = metadata_path.with_name(f".{metadata_path.name}.{nonce}.tmp")
        with vector_temporary.open("wb") as handle:
            np.savez_compressed(handle, vector=np.asarray(vector, dtype=np.float32))
        vector_sha256 = hashlib.sha256(vector_temporary.read_bytes()).hexdigest()
        metadata_temporary.write_bytes(
            canonical_json_bytes(
                {
                    "dimension": self.dimension,
                    "model": self.model,
                    "task_type": task_type,
                    "text_sha256": text_sha256,
                    "vector_sha256": vector_sha256,
                }
            )
        )
        vector_temporary.replace(vector_path)
        metadata_temporary.replace(metadata_path)

    async def _embed_one(self, text: str, task_type: str) -> np.ndarray:
        cached = await asyncio.to_thread(self._load_cached, text, task_type)
        if cached is not None:
            return cached
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
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise EmbeddingUnavailableError("Gemini returned a zero embedding")
        normalized = vector / norm
        await asyncio.to_thread(self._store_cached, text, task_type, normalized)
        return normalized

    async def embed_query(self, text: str) -> np.ndarray:
        return await self._embed_one(text, "RETRIEVAL_QUERY")

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        if len(texts) > 20:
            raise ValueError("at most 20 uncached document embeddings are allowed")
        return await asyncio.gather(
            *(self._embed_one(text, "RETRIEVAL_DOCUMENT") for text in texts)
        )
