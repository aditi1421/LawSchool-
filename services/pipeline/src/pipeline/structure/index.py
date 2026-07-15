"""Chunk index: retrieval over a matter's chunks.

`ChunkIndex` is the seam where a managed vector store (pgvector + embeddings)
plugs in for production. `LexicalChunkIndex` is the offline baseline — BM25-ish
token scoring, no external services — good enough for dev, tests, and small
matters, and it keeps the whole pipeline runnable without credentials.
"""

import math
from collections import Counter
from typing import Protocol

from pipeline.evals.judge import _STOPWORDS, _tokens
from pipeline.models import Chunk


class ChunkIndex(Protocol):
    def add(self, chunks: list[Chunk]) -> None: ...
    def search(self, query: str, k: int = 8) -> list[Chunk]: ...


class LexicalChunkIndex:
    """TF-IDF-weighted token overlap. Deterministic, offline, dependency-free."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._doc_tokens: list[set[str]] = []
        self._df: Counter[str] = Counter()

    def add(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            tokens = _tokens(chunk.text) - _STOPWORDS
            self._chunks.append(chunk)
            self._doc_tokens.append(tokens)
            self._df.update(tokens)

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        query_tokens = _tokens(query) - _STOPWORDS
        if not query_tokens or not self._chunks:
            return []
        n = len(self._chunks)
        scored: list[tuple[float, int]] = []
        for i, tokens in enumerate(self._doc_tokens):
            score = sum(
                math.log(1 + n / self._df[t]) for t in query_tokens if t in tokens
            )
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self._chunks[i] for _, i in scored[:k]]
