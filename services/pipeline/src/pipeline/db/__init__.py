"""Database layer: Postgres + pgvector.

The filesystem MatterStore was a v1 shortcut — unencrypted files on one
machine, no isolation, no backups. This package is the durable replacement:
matters, documents, pages, chunks (with embeddings), artifacts and drafts in
Postgres; the PDFs themselves in object storage (see pipeline.storage).
"""

from pipeline.db.engine import get_engine, get_session, session_scope
from pipeline.db.models import (
    Base,
    DocumentRow,
    DraftRow,
    MatterArtifactsRow,
    MatterRow,
    PageRow,
    ChunkRow,
)

__all__ = [
    "Base",
    "ChunkRow",
    "DocumentRow",
    "DraftRow",
    "MatterArtifactsRow",
    "MatterRow",
    "PageRow",
    "get_engine",
    "get_session",
    "session_scope",
]
