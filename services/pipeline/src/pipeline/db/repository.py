"""Postgres-backed matter repository.

Deliberately mirrors the old filesystem MatterStore's interface (create/get/
list_matters/delete/add_pdf/load_pages/ocr_document/remove_document) so the
API and services swap over without a rewrite. What changes underneath:
PDFs go to object storage, everything else to Postgres, and chunks are
computed and embedded at ingest instead of on every read.
"""

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from pipeline.db.engine import session_scope
from pipeline.db.models import (
    ChunkRow,
    DocumentRow,
    DraftRow,
    MatterArtifactsRow,
    MatterRow,
    PageRow,
)
from pipeline.embeddings import Embedder
from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import OcrEngine, PageExtract, extract_pages
from pipeline.ingest.matter import DocumentRecord, MatterManifest, OcrStatus, PageRecord
from pipeline.models import Chunk, Citation, DocType, Language
from pipeline.storage import Storage
from pipeline.structure import chunk_pages


def storage_key(matter_id: str, filename: str) -> str:
    return f"matters/{matter_id}/{filename}"


def _to_record(doc: DocumentRow) -> DocumentRecord:
    return DocumentRecord(
        file=doc.filename,
        doc_type=DocType(doc.doc_type),
        pages=[
            PageRecord(
                page=p.page_no,
                method=p.method,
                confidence=p.confidence,
                language=Language(p.language),
                chars=len(p.text),
            )
            for p in doc.pages
        ],
        needs_ocr_pages=[p.page_no for p in doc.pages if p.method == "needs_ocr"],
        ocr_status=doc.ocr_status,  # type: ignore[arg-type]
        ocr_error=doc.ocr_error,
    )


def _to_manifest(row: MatterRow) -> MatterManifest:
    return MatterManifest(
        matter_id=row.id,
        title=row.title,
        created=row.created,
        documents=[_to_record(d) for d in row.documents],
    )


class MatterRepository:
    def __init__(self, storage: Storage, embedder: Embedder | None = None) -> None:
        self.storage = storage
        self.embedder = embedder

    # -- lifecycle ----------------------------------------------------------
    def create(self, title: str, today: date) -> MatterManifest:
        matter_id = uuid.uuid4().hex[:12]
        with session_scope() as s:
            s.add(MatterRow(id=matter_id, title=title, created=today))
        return MatterManifest(matter_id=matter_id, title=title, created=today)

    def _matter(self, s: Session, matter_id: str) -> MatterRow:
        row = s.execute(
            select(MatterRow)
            .options(selectinload(MatterRow.documents).selectinload(DocumentRow.pages))
            .where(MatterRow.id == matter_id)
        ).scalar_one_or_none()
        if row is None:
            raise FileNotFoundError(matter_id)
        return row

    def get(self, matter_id: str) -> MatterManifest:
        with session_scope() as s:
            return _to_manifest(self._matter(s, matter_id))

    def list_matters(self) -> list[MatterManifest]:
        with session_scope() as s:
            rows = s.execute(
                select(MatterRow)
                .options(selectinload(MatterRow.documents).selectinload(DocumentRow.pages))
                .order_by(MatterRow.created_at.desc())
            ).scalars().all()
            return [_to_manifest(r) for r in rows]

    def delete(self, matter_id: str) -> None:
        """Hard delete — DPDP 'delete my matter' must leave nothing behind."""
        with session_scope() as s:
            row = self._matter(s, matter_id)
            s.delete(row)  # cascades to documents/pages/chunks/artifacts/drafts
        self.storage.delete_prefix(f"matters/{matter_id}")

    # -- ingestion ----------------------------------------------------------
    def add_pdf(self, matter_id: str, filename: str, content: bytes) -> DocumentRecord:
        """Store the PDF and read its text layer. Fast — never runs OCR."""
        key = storage_key(matter_id, filename)
        self.storage.put(key, content)
        try:
            pages = extract_pages_from_bytes(content, filename)
        except Exception:
            self.storage.delete(key)  # don't keep a file we can't read
            raise
        return self._persist_document(matter_id, filename, key, pages)

    def _persist_document(
        self,
        matter_id: str,
        filename: str,
        key: str,
        pages: list[PageExtract],
        ocr_status: OcrStatus | None = None,
    ) -> DocumentRecord:
        head = "\n".join(p.text for p in pages[:3])
        doc_type = classify_doc_type(head)
        needs_ocr = [p.page for p in pages if p.method == "needs_ocr"]
        status: OcrStatus = ocr_status or ("pending" if needs_ocr else "not_needed")

        chunks = chunk_pages(matter_id, filename, doc_type, pages)
        vectors = self._embed([c.text for c in chunks])

        with session_scope() as s:
            self._matter(s, matter_id)  # 404 if the matter is gone
            s.execute(
                delete(DocumentRow).where(
                    DocumentRow.matter_id == matter_id, DocumentRow.filename == filename
                )
            )
            s.flush()
            doc = DocumentRow(
                matter_id=matter_id,
                filename=filename,
                doc_type=doc_type.value,
                storage_key=key,
                ocr_status=status,
                ocr_error=None,
            )
            doc.pages = [
                PageRow(
                    page_no=p.page,
                    text=p.text,
                    method=p.method,
                    confidence=p.confidence,
                    language=p.language.value,
                )
                for p in pages
            ]
            s.add(doc)
            s.flush()
            for chunk, vec in zip(chunks, vectors, strict=True):
                s.add(
                    ChunkRow(
                        matter_id=matter_id,
                        document_id=doc.id,
                        filename=chunk.location.file,
                        page_no=chunk.location.page,
                        para=chunk.location.para,
                        text=chunk.text,
                        doc_type=chunk.doc_type.value,
                        language=chunk.language.value,
                        ocr_confidence=chunk.ocr_confidence,
                        embedding=vec,
                    )
                )
            s.flush()
            s.refresh(doc)
            return _to_record(doc)

    def _embed(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        if self.embedder is None:
            return [None] * len(texts)
        try:
            return list(self.embedder.embed_documents(texts))  # type: ignore[arg-type]
        except Exception:
            # Embedding failure must not lose the document — retrieval falls
            # back to lexical for these chunks.
            return [None] * len(texts)

    def remove_document(self, matter_id: str, filename: str) -> None:
        with session_scope() as s:
            row = self._matter(s, matter_id)
            doc = next((d for d in row.documents if d.filename == filename), None)
            if doc is None:
                raise FileNotFoundError(filename)
            s.delete(doc)  # cascades to pages + chunks
        self.storage.delete(storage_key(matter_id, filename))

    # -- OCR ----------------------------------------------------------------
    def set_ocr_status(
        self, matter_id: str, filename: str, status: OcrStatus, error: str | None = None
    ) -> None:
        with session_scope() as s:
            doc = s.execute(
                select(DocumentRow).where(
                    DocumentRow.matter_id == matter_id, DocumentRow.filename == filename
                )
            ).scalar_one_or_none()
            if doc is not None:
                doc.ocr_status = status
                doc.ocr_error = error

    def ocr_document(self, matter_id: str, filename: str, ocr: OcrEngine) -> DocumentRecord:
        """Re-read a stored PDF with OCR. Blocking and slow — call off the
        request path."""
        self.set_ocr_status(matter_id, filename, "running")
        try:
            key = storage_key(matter_id, filename)
            content = self.storage.get(key)
            pages = extract_pages_from_bytes(content, filename, ocr=ocr)
            return self._persist_document(matter_id, filename, key, pages, ocr_status="done")
        except Exception as exc:
            self.set_ocr_status(matter_id, filename, "failed", str(exc))
            raise

    # -- reads --------------------------------------------------------------
    def load_pages(self, matter_id: str, filename: str) -> list[PageExtract]:
        with session_scope() as s:
            doc = s.execute(
                select(DocumentRow)
                .options(selectinload(DocumentRow.pages))
                .where(
                    DocumentRow.matter_id == matter_id, DocumentRow.filename == filename
                )
            ).scalar_one_or_none()
            if doc is None:
                raise FileNotFoundError(filename)
            return [
                PageExtract(
                    page=p.page_no,
                    text=p.text,
                    method=p.method,  # type: ignore[arg-type]
                    confidence=p.confidence,
                    language=Language(p.language),
                )
                for p in doc.pages
            ]

    def file_bytes(self, matter_id: str, filename: str) -> bytes:
        return self.storage.get(storage_key(matter_id, filename))

    def matter_chunks(self, matter_id: str) -> list[Chunk]:
        with session_scope() as s:
            rows = s.execute(
                select(ChunkRow)
                .where(ChunkRow.matter_id == matter_id)
                .order_by(ChunkRow.filename, ChunkRow.page_no, ChunkRow.para)
            ).scalars().all()
            return [_chunk(r) for r in rows]

    def search(self, matter_id: str, query: str, k: int = 8) -> list[Chunk]:
        """Semantic search scoped to one matter.

        Scoping is a security boundary, not an optimisation: a lawyer must
        never retrieve another matter's text. Falls back to lexical when the
        matter has no embeddings.
        """
        if self.embedder is not None:
            try:
                qvec = self.embedder.embed_query(query)
            except Exception:
                qvec = None
            if qvec is not None:
                with session_scope() as s:
                    rows = s.execute(
                        select(ChunkRow)
                        .where(
                            ChunkRow.matter_id == matter_id,
                            ChunkRow.embedding.is_not(None),
                        )
                        .order_by(ChunkRow.embedding.cosine_distance(qvec))
                        .limit(k)
                    ).scalars().all()
                if rows:
                    return [_chunk(r) for r in rows]

        from pipeline.structure import LexicalChunkIndex

        index = LexicalChunkIndex()
        index.add(self.matter_chunks(matter_id))
        return index.search(query, k=k)

    # -- artifacts / drafts -------------------------------------------------
    def save_artifacts(self, matter_id: str, data: dict) -> None:
        with session_scope() as s:
            existing = s.get(MatterArtifactsRow, matter_id)
            if existing is None:
                s.add(MatterArtifactsRow(matter_id=matter_id, data=data))
            else:
                existing.data = data

    def load_artifacts(self, matter_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(MatterArtifactsRow, matter_id)
            return dict(row.data) if row else None

    def save_draft(self, matter_id: str, doc_type: str, data: dict) -> str:
        draft_id = uuid.uuid4().hex[:12]
        with session_scope() as s:
            s.add(DraftRow(id=draft_id, matter_id=matter_id, doc_type=doc_type, data=data))
        return draft_id

    def load_draft(self, matter_id: str, draft_id: str) -> dict:
        with session_scope() as s:
            row = s.execute(
                select(DraftRow).where(
                    DraftRow.id == draft_id, DraftRow.matter_id == matter_id
                )
            ).scalar_one_or_none()
            if row is None:
                raise FileNotFoundError(draft_id)
            return dict(row.data)

    def list_drafts(self, matter_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(
                select(DraftRow)
                .where(DraftRow.matter_id == matter_id)
                .order_by(DraftRow.created_at.desc())
            ).scalars().all()
            return [
                {
                    "draft_id": r.id,
                    "doc_type": r.doc_type,
                    "title": r.data.get("title", ""),
                    "paragraphs": len(r.data.get("paragraphs", [])),
                    "missing_info": len(r.data.get("missing_info", [])),
                }
                for r in rows
            ]


def _chunk(row: ChunkRow) -> Chunk:
    return Chunk(
        matter_id=row.matter_id,
        location=Citation(file=row.filename, page=row.page_no, para=row.para),
        text=row.text,
        doc_type=DocType(row.doc_type),
        language=Language(row.language),
        ocr_confidence=row.ocr_confidence,
    )


def extract_pages_from_bytes(
    content: bytes, filename: str, ocr: OcrEngine | None = None
) -> list[PageExtract]:
    """extract_pages works on a path; PDFs now live in object storage, so
    stage the bytes to a temp file for PyMuPDF."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        path.write_bytes(content)
        return extract_pages(path, ocr=ocr)
