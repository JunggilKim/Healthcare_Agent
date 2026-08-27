from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import numpy as np
import orjson
from google import genai
from google.api_core.exceptions import PreconditionFailed
from google.cloud.storage import Bucket  # type: ignore[import-untyped]
from google.genai import types

from backend.app.domain.canonical import canonical_json_bytes

logger = logging.getLogger("trial_opt.live")


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
        shared_cache_bucket: Bucket | None = None,
        cache_namespace: str = "v1",
    ) -> None:
        self.client = client
        self.model = model
        self.dimension = dimension
        self._semaphore = asyncio.Semaphore(concurrency)
        self.cache_root = cache_root
        self.shared_cache_bucket = shared_cache_bucket
        normalized_namespace = cache_namespace.strip("/")
        if not normalized_namespace or any(
            part in {"", ".", ".."} for part in normalized_namespace.split("/")
        ):
            raise ValueError("embedding cache namespace must be a safe non-empty path")
        self.cache_namespace = normalized_namespace

    def _cache_identity(self, text: str, task_type: str) -> tuple[str, str]:
        text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        key = hashlib.sha256(
            f"{self.model}\0{self.dimension}\0{task_type}\0{text_sha256}".encode()
        ).hexdigest()
        return text_sha256, key

    def _cache_paths(self, text: str, task_type: str) -> tuple[str, Path, Path] | None:
        if self.cache_root is None:
            return None
        text_sha256, key = self._cache_identity(text, task_type)
        return (
            text_sha256,
            self.cache_root / f"{key}.npz",
            self.cache_root / f"{key}.json",
        )

    def _shared_object_name(self, text: str, task_type: str) -> str:
        _, key = self._cache_identity(text, task_type)
        return f"embedding-cache/{self.cache_namespace}/{key}.json"

    def _load_shared_cached(self, text: str, task_type: str) -> np.ndarray | None:
        if self.shared_cache_bucket is None:
            return None
        text_sha256, _ = self._cache_identity(text, task_type)
        try:
            payload = orjson.loads(
                self.shared_cache_bucket.blob(
                    self._shared_object_name(text, task_type)
                ).download_as_bytes()
            )
            expected_metadata = {
                "dimension": self.dimension,
                "model": self.model,
                "task_type": task_type,
                "text_sha256": text_sha256,
            }
            if any(payload.get(key) != value for key, value in expected_metadata.items()):
                return None
            vector = np.asarray(payload["vector"], dtype=np.float64)
            vector_bytes = np.asarray(vector, dtype=np.float32).tobytes()
            if payload.get("vector_sha256") != hashlib.sha256(vector_bytes).hexdigest():
                return None
        except Exception as error:
            # A shared cache outage must never disable Live retrieval. Provider
            # dispatch remains available and will repopulate the cache later.
            if type(error).__name__ not in {"NotFound", "Forbidden"}:
                logger.warning(
                    "shared embedding cache read failed; continuing without cache (error=%s)",
                    type(error).__name__,
                )
            return None
        if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
            return None
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else None

    def _store_shared_cached(self, text: str, task_type: str, vector: np.ndarray) -> None:
        if self.shared_cache_bucket is None:
            return
        text_sha256, _ = self._cache_identity(text, task_type)
        float_vector = np.asarray(vector, dtype=np.float32)
        payload = canonical_json_bytes(
            {
                "dimension": self.dimension,
                "model": self.model,
                "task_type": task_type,
                "text_sha256": text_sha256,
                "vector": float_vector.tolist(),
                "vector_sha256": hashlib.sha256(float_vector.tobytes()).hexdigest(),
            }
        )
        try:
            self.shared_cache_bucket.blob(
                self._shared_object_name(text, task_type)
            ).upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            # Content-addressed writers are intentionally idempotent.
            return
        except Exception as error:
            logger.warning(
                "shared embedding cache write failed; returning embedding (error=%s)",
                type(error).__name__,
            )

    def _load_cached(self, text: str, task_type: str) -> np.ndarray | None:
        paths = self._cache_paths(text, task_type)
        if paths is None:
            return self._load_shared_cached(text, task_type)
        text_sha256, vector_path, metadata_path = paths
        if not vector_path.is_file() or not metadata_path.is_file():
            shared = self._load_shared_cached(text, task_type)
            if shared is not None:
                self._store_local_cached(text, task_type, shared)
            return shared
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

    def _store_local_cached(self, text: str, task_type: str, vector: np.ndarray) -> None:
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

    def _store_cached(self, text: str, task_type: str, vector: np.ndarray) -> None:
        self._store_local_cached(text, task_type, vector)
        self._store_shared_cached(text, task_type, vector)

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
        if not texts:
            return []

        cached = await asyncio.gather(
            *(asyncio.to_thread(self._load_cached, text, "RETRIEVAL_DOCUMENT") for text in texts)
        )
        results: list[np.ndarray | None] = list(cached)
        missing_indexes = [index for index, vector in enumerate(results) if vector is None]
        if not missing_indexes:
            return cast(list[np.ndarray], results)

        missing_texts = [texts[index] for index in missing_indexes]
        async with self._semaphore:
            try:
                response = await self.client.aio.models.embed_content(
                    model=self.model,
                    # The SDK accepts a list of strings at runtime, but its nested
                    # invariant input union does not type-check as list[str].
                    contents=cast(Any, missing_texts),
                    config=types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=self.dimension,
                    ),
                )
            except Exception as error:
                raise EmbeddingUnavailableError("Gemini embedding batch request failed") from error

        embeddings = response.embeddings or []
        if len(embeddings) != len(missing_indexes):
            raise EmbeddingUnavailableError("Gemini returned an incomplete embedding batch")

        writes = []
        for index, embedding in zip(missing_indexes, embeddings, strict=True):
            if embedding.values is None:
                raise EmbeddingUnavailableError("Gemini returned no document embedding")
            vector = np.asarray(embedding.values, dtype=np.float64)
            if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
                raise EmbeddingUnavailableError("Gemini returned an invalid document embedding")
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                raise EmbeddingUnavailableError("Gemini returned a zero document embedding")
            normalized = vector / norm
            results[index] = normalized
            writes.append(
                asyncio.to_thread(
                    self._store_cached,
                    texts[index],
                    "RETRIEVAL_DOCUMENT",
                    normalized,
                )
            )
        await asyncio.gather(*writes)
        return cast(list[np.ndarray], results)
