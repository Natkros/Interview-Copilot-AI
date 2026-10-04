"""BM25 keyword retrieval over a candidate's chunks (and the shared technical
knowledge base). Corpora are small (hundreds of chunks), so indexes are built
in memory and cached per candidate until the knowledge base changes."""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass

from app.rag.embeddings import tokenize


@dataclass
class KeywordDoc:
    id: str
    collection: str
    source_type: str
    text: str
    verified: bool
    meta: dict


class BM25Index:
    def __init__(self, docs: list[KeywordDoc], k1: float = 1.4, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d.text)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avgdl = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, limit: int = 10, collections: set[str] | None = None,
               where: dict | None = None) -> list[tuple[KeywordDoc, float]]:
        q = [t for t in tokenize(query) if t in self.idf]
        if not q:
            return []
        scored = []
        for doc, tf, dl in zip(self.docs, self.tfs, self.lens, strict=True):
            if collections and doc.collection not in collections:
                continue
            if where and any(doc.meta.get(k) != v for k, v in where.items()):
                continue
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((doc, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]


_cache: dict[str, BM25Index] = {}
_lock = threading.Lock()
_global_index: BM25Index | None = None


def invalidate_keyword_cache(candidate_id: str) -> None:
    with _lock:
        _cache.pop(candidate_id, None)


def candidate_index(candidate_id: str, loader) -> BM25Index:
    """`loader()` returns the candidate's KeywordDocs (called on cache miss)."""
    with _lock:
        idx = _cache.get(candidate_id)
    if idx is None:
        idx = BM25Index(loader())
        with _lock:
            _cache[candidate_id] = idx
    return idx


def global_technical_index() -> BM25Index:
    global _global_index
    if _global_index is None:
        from app.rag.indexer import load_technical_knowledge

        docs = [
            KeywordDoc(id=e["id"], collection="technical_knowledge", source_type="technical",
                       text=f"{e['title']}: {e['text']}", verified=False,
                       meta={"section": e["topic"], "title": e["title"], "source": "Technical knowledge base",
                             "kb_id": e["id"]})
            for e in load_technical_knowledge()
        ]
        _global_index = BM25Index(docs)
    return _global_index
