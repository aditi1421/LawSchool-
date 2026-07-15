"""Embedding providers for semantic retrieval.

The v1 query path used keyword overlap (LexicalChunkIndex) — a placeholder
that misses a question phrased differently from the record, which is the norm
in legal text ("who owns the suit property" vs "absolute owner thereof").

Anthropic does not serve an embeddings endpoint; Voyage is the recommended
provider and ships `voyage-law-2`, tuned for legal retrieval — the right
production default here. LocalEmbedder keeps the pipeline runnable (and the
documents on-machine) without an API key.
"""

import os
from typing import Protocol

from pipeline.db.models import EMBEDDING_DIM


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
    """voyage-law-2 — legal-domain embeddings, 1024-dim. Production default.

    Note this sends chunk text to Voyage. For clients who refuse third-party
    processing of privileged material, use LocalEmbedder instead.
    """

    dim = EMBEDDING_DIM

    def __init__(self, model: str = "voyage-law-2") -> None:
        import voyageai

        self._client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        self._model = model

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        # Voyage caps batch size; chunk conservatively.
        for i in range(0, len(texts), 96):
            batch = texts[i : i + 96]
            res = self._client.embed(batch, model=self._model, input_type=input_type)
            out.extend(res.embeddings)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


class LocalEmbedder:
    """On-machine embeddings — no API key, no document leaves the host.

    Uses a multilingual sentence-transformers model (English + Hindi, matching
    the OCR languages). Padded/truncated to EMBEDDING_DIM so the column shape
    is provider-independent and swapping providers needs no migration — only a
    re-embed.
    """

    dim = EMBEDDING_DIM

    def __init__(self, model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)

    def _fit(self, vec: list[float]) -> list[float]:
        if len(vec) >= EMBEDDING_DIM:
            return vec[:EMBEDDING_DIM]
        return vec + [0.0] * (EMBEDDING_DIM - len(vec))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [self._fit(list(map(float, v))) for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def get_embedder() -> Embedder | None:
    """Resolve the configured embedder, or None if none is available.

    None is not an error: chunks stay un-embedded and retrieval falls back to
    the lexical index, which is worse but correct.
    """
    provider = os.environ.get("LAWSCHOOL_EMBEDDINGS", "auto").lower()
    if provider in {"none", "off"}:
        return None
    if provider in {"voyage", "auto"} and os.environ.get("VOYAGE_API_KEY"):
        try:
            return VoyageEmbedder()
        except Exception:
            if provider == "voyage":
                raise
    if provider in {"local", "auto"}:
        try:
            return LocalEmbedder()
        except Exception:
            if provider == "local":
                raise
    return None
