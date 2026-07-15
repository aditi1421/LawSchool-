"""SQLAlchemy models.

Design notes:
- `matter_id` is carried on chunks as well as through documents so the hot
  retrieval query (chunks for one matter) never needs a join.
- Embeddings live beside their text in the same row: one database to back up,
  one place data can leak from, and no sync problem between a vector store and
  a source of truth.
- Deleting a matter cascades to everything under it — DPDP "delete my matter"
  must leave nothing behind.
- Case-file bytes are NOT here; `DocumentRow.storage_key` points into object
  storage. Blobs in Postgres bloat backups and complicate encryption.
"""

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# voyage-law-2 and most legal-tuned embedding models are 1024-dimensional.
# Changing this requires a migration and a re-embed of every chunk.
EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


class MatterRow(Base):
    __tablename__ = "matters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    created: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list["DocumentRow"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan", order_by="DocumentRow.uploaded_at"
    )
    chunks: Mapped[list["ChunkRow"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    artifacts: Mapped["MatterArtifactsRow | None"] = relationship(
        back_populates="matter", cascade="all, delete-orphan", uselist=False
    )
    drafts: Mapped[list["DraftRow"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan", order_by="DraftRow.created_at.desc()"
    )


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("matter_id", "filename", name="uq_document_per_matter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(32))
    # Where the PDF bytes live in object storage, not the bytes themselves.
    storage_key: Mapped[str] = mapped_column(String(1024))
    ocr_status: Mapped[str] = mapped_column(String(16), default="not_needed")
    ocr_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matter: Mapped["MatterRow"] = relationship(back_populates="documents")
    pages: Mapped[list["PageRow"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="PageRow.page_no"
    )
    chunks: Mapped[list["ChunkRow"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PageRow(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_page_per_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(16))  # text_layer | ocr | needs_ocr
    confidence: Mapped[float] = mapped_column(Float)
    language: Mapped[str] = mapped_column(String(8))

    document: Mapped["DocumentRow"] = relationship(back_populates="pages")


class ChunkRow(Base):
    """A provenance-carrying unit of text, with its embedding."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Retrieval is always scoped to one matter — a lawyer must never see
    # another matter's text — so this filter is on every read path.
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Provenance — the citation this chunk can support.
    filename: Mapped[str] = mapped_column(String(512))
    page_no: Mapped[int] = mapped_column(Integer)
    para: Mapped[int | None] = mapped_column(Integer, nullable=True)

    text: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8))
    ocr_confidence: Mapped[float] = mapped_column(Float)
    # Null until embedded — retrieval falls back to lexical for un-embedded chunks.
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    matter: Mapped["MatterRow"] = relationship(back_populates="chunks")
    document: Mapped["DocumentRow"] = relationship(back_populates="chunks")


class MatterArtifactsRow(Base):
    """The generated brief. Stored whole as JSONB — it is read and written as
    one document and its shape is owned by the Pydantic model, not the DB."""

    __tablename__ = "matter_artifacts"

    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matter: Mapped["MatterRow"] = relationship(back_populates="artifacts")


class DraftRow(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matter: Mapped["MatterRow"] = relationship(back_populates="drafts")
