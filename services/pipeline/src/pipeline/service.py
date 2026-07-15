"""Matter-level orchestration: record -> chunks -> artifacts / query.

Thin by design: retrieval and persistence belong to the repository, grounding
to the agents. This module only sequences them.
"""

from pipeline.artifacts.generate import ArtifactModel, GroundingViolation, generate_artifacts
from pipeline.db.repository import MatterRepository
from pipeline.models import Chunk, MatterArtifacts


def matter_chunks(store: MatterRepository, matter_id: str) -> list[Chunk]:
    """All provenance-carrying chunks for a matter."""
    return store.matter_chunks(matter_id)


def run_artifacts(
    store: MatterRepository, matter_id: str, model: ArtifactModel
) -> tuple[MatterArtifacts, list[GroundingViolation]]:
    """Generate, honesty-check, and persist the matter's artifacts."""
    chunks = store.matter_chunks(matter_id)
    if not chunks:
        raise ValueError("matter has no readable content — upload documents first")
    artifacts, violations = generate_artifacts(matter_id, chunks, model)
    store.save_artifacts(matter_id, artifacts.model_dump(mode="json"))
    return artifacts, violations


def load_artifacts(store: MatterRepository, matter_id: str) -> MatterArtifacts | None:
    data = store.load_artifacts(matter_id)
    return MatterArtifacts.model_validate(data) if data else None


def retrieve(store: MatterRepository, matter_id: str, question: str, k: int = 8) -> list[Chunk]:
    """Top-k chunks for a question — semantic when embedded, lexical otherwise."""
    return store.search(matter_id, question, k=k)
