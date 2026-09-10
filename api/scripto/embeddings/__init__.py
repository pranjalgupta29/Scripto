"""Embedding providers. Start with Voyage, keep it swappable."""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx

from scripto.config import settings


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        ...

    @property
    def dim(self) -> int:
        return settings.embedding_dim


class VoyageProvider(EmbeddingProvider):
    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            json={
                "input": texts,
                "model": settings.embedding_model,
                "input_type": input_type,
                "output_dimension": settings.embedding_dim,
            },
            timeout=settings.http_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Google embeddings via the native batch endpoint.

    Uses `outputDimensionality` so the vector matches the schema's Vector(1024)
    and no migration is needed when switching from another provider. Google
    recommends renormalising when the dimension is truncated, so we do.
    """

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
    BATCH = 100

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []

        model = settings.embedding_model
        task = (
            "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            batch = texts[start : start + self.BATCH]
            response = httpx.post(
                f"{self.ENDPOINT}/{model}:batchEmbedContents",
                headers={"x-goog-api-key": settings.embedding_api_key or ""},
                json={
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": task,
                            "outputDimensionality": settings.embedding_dim,
                        }
                        for text in batch
                    ]
                },
                timeout=settings.http_timeout_seconds * 2,
            )
            response.raise_for_status()
            for item in response.json().get("embeddings", []):
                vectors.append(_normalise(item["values"]))

        return vectors


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed bag-of-words vectors.

    Not semantically meaningful in general, but similar strings do produce
    similar vectors, which is enough for clustering logic to be exercised and
    tested end to end without a network call.
    """

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = [t for t in text.lower().split() if t]
        for token in tokens:
            idx = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big") % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


@lru_cache
def get_embedder() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider == "voyage":
        return VoyageProvider()
    if provider in ("gemini", "google"):
        return GeminiEmbeddingProvider()
    if provider == "fake":
        return FakeEmbeddingProvider()
    raise ValueError(f"unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")
