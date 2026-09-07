from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select, text

from .control_db import KnowledgeEmbedding

_WORD = re.compile(r"[A-Za-z0-9_./:-]{2,}")


def _unit(values: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in values))
    return [v / n for v in values] if n else values


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def _hash_embedding(value: str, dimensions: int) -> list[float]:
    # Portable fallback used for tests/offline installs. Production deployments can
    # point SMART_ROUTER_EMBEDDINGS_BASE_URL at any OpenAI-compatible embeddings API.
    vec = [0.0] * dimensions
    tokens = [x.lower() for x in _WORD.findall(value)]
    for index, token in enumerate(tokens):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = -1.0 if digest[4] & 1 else 1.0
        vec[bucket] += sign * (1.0 + min(3.0, len(token) / 8.0))
        if index:
            bigram = tokens[index - 1] + "\x00" + token
            bd = hashlib.blake2b(bigram.encode("utf-8"), digest_size=8).digest()
            vec[int.from_bytes(bd[:4], "big") % dimensions] += (-0.45 if bd[4] & 1 else 0.45)
    return _unit(vec)


@dataclass
class EmbeddingStatus:
    provider: str
    model: str
    dimensions: int
    pgvector: bool
    fallback_used: bool = False
    last_error: str = ""


class EmbeddingProvider:
    def __init__(self) -> None:
        self.base_url = os.getenv("SMART_ROUTER_EMBEDDINGS_BASE_URL", "").strip().rstrip("/")
        self.model = os.getenv("SMART_ROUTER_EMBEDDINGS_MODEL", "text-embedding-3-small").strip()
        self.api_key = os.getenv("SMART_ROUTER_EMBEDDINGS_API_KEY", "").strip()
        try:
            self.dimensions = max(32, min(4096, int(os.getenv("SMART_ROUTER_EMBEDDINGS_DIMENSIONS", "384"))))
        except ValueError:
            self.dimensions = 384
        self.last_error = ""
        self.fallback_used = False

    @property
    def provider_name(self) -> str:
        return "openai-compatible" if self.base_url else "portable-hash-fallback"

    def embed(self, value: str) -> list[float]:
        if self.base_url:
            try:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                with httpx.Client(timeout=float(os.getenv("SMART_ROUTER_EMBEDDINGS_TIMEOUT_SECONDS", "20"))) as client:
                    response = client.post(
                        self.base_url + "/embeddings",
                        headers=headers,
                        json={"model": self.model, "input": value},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    vector = payload["data"][0]["embedding"]
                    if not isinstance(vector, list) or not vector:
                        raise ValueError("embedding API returned an empty vector")
                    result = [float(x) for x in vector]
                    self.dimensions = len(result)
                    self.last_error = ""
                    return _unit(result)
            except Exception as exc:  # inference must stay available if embeddings are down
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                self.fallback_used = True
        return _hash_embedding(value, self.dimensions)


class VectorIndex:
    def __init__(self, store: Any):
        self.store = store
        self.provider = EmbeddingProvider()
        self.pgvector_enabled = False
        if str(store.url).startswith(("postgresql", "postgres")):
            self._enable_pgvector()

    def _enable_pgvector(self) -> None:
        dim = self.provider.dimensions
        try:
            with self.store.engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.execute(text(f"ALTER TABLE v56_knowledge_embeddings ADD COLUMN IF NOT EXISTS embedding_vector vector({dim})"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_v56_knowledge_embeddings_kb ON v56_knowledge_embeddings (kb_id)"))
            self.pgvector_enabled = True
        except Exception as exc:
            self.provider.last_error = f"pgvector unavailable: {type(exc).__name__}: {exc}"[:500]
            self.pgvector_enabled = False

    def status(self) -> EmbeddingStatus:
        return EmbeddingStatus(
            provider=self.provider.provider_name,
            model=self.provider.model,
            dimensions=self.provider.dimensions,
            pgvector=self.pgvector_enabled,
            fallback_used=self.provider.fallback_used,
            last_error=self.provider.last_error,
        )

    def index_chunk(self, chunk_id: int, kb_id: int, content: str) -> None:
        vector = self.provider.embed(content)
        payload = json.dumps(vector, separators=(",", ":"))
        with self.store.session() as session:
            row = session.scalar(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == chunk_id))
            if row is None:
                row = KnowledgeEmbedding(chunk_id=chunk_id, kb_id=kb_id, embedding_json=payload, dimensions=len(vector))
                session.add(row)
            else:
                row.kb_id = kb_id
                row.embedding_json = payload
                row.dimensions = len(vector)
            session.commit()
        if self.pgvector_enabled:
            vec = "[" + ",".join(f"{x:.9g}" for x in vector) + "]"
            try:
                with self.store.engine.begin() as conn:
                    conn.execute(
                        text("UPDATE v56_knowledge_embeddings SET embedding_vector=CAST(:vector AS vector) WHERE chunk_id=:chunk_id"),
                        {"vector": vec, "chunk_id": chunk_id},
                    )
            except Exception as exc:
                self.provider.last_error = f"pgvector write failed: {type(exc).__name__}: {exc}"[:500]

    def delete_kb(self, kb_id: int) -> None:
        with self.store.session() as session:
            rows = list(session.scalars(select(KnowledgeEmbedding).where(KnowledgeEmbedding.kb_id == kb_id)))
            for row in rows:
                session.delete(row)
            session.commit()

    def search(self, kb_ids: list[int], query: str, limit: int = 50) -> dict[int, float]:
        if not kb_ids or not query.strip():
            return {}
        qv = self.provider.embed(query)
        if self.pgvector_enabled and len(qv) == self.provider.dimensions:
            try:
                vec = "[" + ",".join(f"{x:.9g}" for x in qv) + "]"
                ids = ",".join(str(int(x)) for x in sorted(set(kb_ids)))
                stmt = text(
                    f"SELECT chunk_id, 1 - (embedding_vector <=> CAST(:vector AS vector)) AS score "
                    f"FROM v56_knowledge_embeddings WHERE kb_id IN ({ids}) AND embedding_vector IS NOT NULL "
                    f"ORDER BY embedding_vector <=> CAST(:vector AS vector) LIMIT :limit"
                )
                with self.store.engine.connect() as conn:
                    rows = conn.execute(stmt, {"vector": vec, "limit": max(1, min(limit, 200))}).all()
                return {int(chunk_id): float(score or 0.0) for chunk_id, score in rows}
            except Exception as exc:
                self.provider.last_error = f"pgvector search failed: {type(exc).__name__}: {exc}"[:500]
        scores: dict[int, float] = {}
        with self.store.session() as session:
            rows = list(session.scalars(select(KnowledgeEmbedding).where(KnowledgeEmbedding.kb_id.in_(kb_ids)).limit(5000)))
        for row in rows:
            try:
                vec = [float(x) for x in json.loads(row.embedding_json)]
            except Exception:
                continue
            scores[row.chunk_id] = _cosine(qv, vec)
        return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True)[: max(1, min(limit, 200))])
