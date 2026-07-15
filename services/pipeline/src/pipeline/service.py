"""Matter-level orchestration: storage -> chunks -> artifacts / query."""

import json

from pipeline.artifacts.generate import (
    ArtifactModel,
    GroundingViolation,
    generate_artifacts,
)
from pipeline.ingest.matter import MatterStore
from pipeline.models import Chunk, MatterArtifacts
from pipeline.structure import LexicalChunkIndex, chunk_pages


def matter_chunks(store: MatterStore, matter_id: str) -> list[Chunk]:
    """All provenance-carrying chunks for a matter, from stored extractions."""
    manifest = store.get(matter_id)
    chunks: list[Chunk] = []
    for doc in manifest.documents:
        pages = store.load_pages(matter_id, doc.file)
        chunks.extend(chunk_pages(matter_id, doc.file, doc.doc_type, pages))
    return chunks


def run_artifacts(
    store: MatterStore,
    matter_id: str,
    model: ArtifactModel,
) -> tuple[MatterArtifacts, list[GroundingViolation]]:
    """Generate, honesty-check, and persist the matter's artifacts."""
    chunks = matter_chunks(store, matter_id)
    if not chunks:
        raise ValueError("matter has no readable content — upload documents first")
    artifacts, violations = generate_artifacts(matter_id, chunks, model)
    path = store._matter_dir(matter_id) / "artifacts.json"
    path.write_text(artifacts.model_dump_json(indent=2))
    return artifacts, violations


def load_artifacts(store: MatterStore, matter_id: str) -> MatterArtifacts | None:
    path = store._matter_dir(matter_id) / "artifacts.json"
    if not path.exists():
        return None
    return MatterArtifacts.model_validate(json.loads(path.read_text()))


def retrieve(store: MatterStore, matter_id: str, question: str, k: int = 8) -> list[Chunk]:
    """Top-k chunks for a question (lexical baseline; vector store swaps in here)."""
    index = LexicalChunkIndex()
    index.add(matter_chunks(store, matter_id))
    return index.search(question, k=k)
