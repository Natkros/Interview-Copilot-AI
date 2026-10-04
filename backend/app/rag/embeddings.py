"""Embedding providers.

- `HashEmbedder` (default, offline): deterministic feature hashing over
  canonicalised word unigrams/bigrams and character trigrams. It is a
  lexical-semantic embedding - robust to aliases ("postgres" ~ "PostgreSQL"),
  morphology and word order, but not a learned semantic model. It needs no
  network or model download, which keeps tests and degraded mode deterministic.
- `OpenAICompatibleEmbedder`: any `/v1/embeddings` compatible endpoint
  (configure EMBEDDING_PROVIDER=openai, EMBEDDING_API_KEY, EMBEDDING_BASE_URL,
  EMBEDDING_MODEL, EMBEDDING_DIM).

Both are wrapped in an LRU cache keyed by text hash.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import OrderedDict
from typing import Protocol

import httpx
import numpy as np

from app.core.config import get_settings
from app.services.taxonomy import _alias_index

_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "is", "are",
    "was", "were", "be", "been", "it", "its", "this", "that", "as", "from", "i", "you", "your", "my",
    "me", "we", "our", "can", "could", "would", "should", "do", "did", "does", "about", "tell", "what",
    "why", "how", "which", "who", "when", "where", "so", "if", "into", "than", "then", "there", "their",
    "they", "them", "he", "she", "his", "her", "us", "will", "have", "has", "had", "not", "no", "yes",
}
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]")


def _stem(tok: str) -> str:
    for suf in ("ization", "ations", "ation", "ments", "ment", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(tok) > len(suf) + 3 and tok.endswith(suf):
            tok = tok[: -len(suf)] + ("y" if suf == "ies" else "")
            break
    # "merge"/"merged" -> "merg", "store"/"stored" -> "stor"
    if len(tok) > 4 and tok.endswith("e"):
        tok = tok[:-1]
    return tok


def tokenize(text: str, keep_stop: bool = False) -> list[str]:
    text = text.lower()
    aliases = _alias_index()
    toks = []
    for raw in _TOKEN.findall(text):
        raw = raw.strip(".-")
        if not raw:
            continue
        canon = aliases.get(raw)
        if canon:
            toks.append(canon.lower().replace(" ", "_"))
            continue
        if not keep_stop and raw in _STOP:
            continue
        toks.append(_stem(raw))
    return toks


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    name = "hash-v1"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _features(self, text: str) -> dict[int, float]:
        toks = tokenize(text)
        feats: dict[int, float] = {}

        def add(key: str, weight: float) -> None:
            h = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            feats[idx] = feats.get(idx, 0.0) + sign * weight

        for t in toks:
            add("u:" + t, 1.0)
            padded = f"#{t}#"
            if len(t) > 3:
                for i in range(len(padded) - 2):
                    add("c:" + padded[i: i + 3], 0.25)
        for a, b in zip(toks, toks[1:], strict=False):
            add(f"b:{a}_{b}", 0.6)
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for idx, w in self._features(text).items():
                vec[idx] += w
            # sublinear damping then L2 normalise
            vec = np.sign(vec) * np.log1p(np.abs(vec))
            norm = float(np.linalg.norm(vec))
            out.append((vec / norm).tolist() if norm > 0 else vec.tolist())
        return out


class OpenAICompatibleEmbedder:
    def __init__(self, api_key: str, model: str, base_url: str, dim: int) -> None:
        self.dim = dim
        self.name = f"openai:{model}"
        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=20.0, headers={"Authorization": f"Bearer {api_key}"}
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 96):
            batch = texts[i: i + 96]
            resp = self._client.post("/embeddings", json={"model": self._model, "input": batch, "dimensions": self.dim})
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out


class CachedEmbedder:
    def __init__(self, inner: Embedder, capacity: int = 20_000) -> None:
        self.inner = inner
        self.dim = inner.dim
        self.name = inner.name
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._capacity = capacity
        self.hits = 0
        self.misses = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [(i, t) for i, (k, t) in enumerate(zip(keys, texts, strict=True)) if k not in self._cache]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing:
            vecs = self.inner.embed([t for _, t in missing])
            for (i, _), v in zip(missing, vecs, strict=True):
                self._cache[keys[i]] = v
                if len(self._cache) > self._capacity:
                    self._cache.popitem(last=False)
        result = []
        for k in keys:
            self._cache.move_to_end(k)
            result.append(self._cache[k])
        return result

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


_embedder: CachedEmbedder | None = None


def get_embedder() -> CachedEmbedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.embedding_provider == "openai" and s.embedding_api_key:
            inner: Embedder = OpenAICompatibleEmbedder(
                s.embedding_api_key, s.embedding_model, s.embedding_base_url, s.embedding_dim
            )
        else:
            inner = HashEmbedder(s.embedding_dim)
        _embedder = CachedEmbedder(inner)
    return _embedder


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
