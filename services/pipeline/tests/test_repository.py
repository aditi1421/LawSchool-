"""Repository tests against real Postgres.

Skipped when no database is reachable (CI without Docker, offline dev) — but
they run against the same engine and DDL production uses, because a repository
mocked away from its database proves nothing about the schema.

    docker compose up -d   # then: uv run pytest tests/test_repository.py
"""

from datetime import date
from pathlib import Path

import pytest
from pipeline.db.engine import session_scope
from pipeline.db.models import ChunkRow, DocumentRow
from pipeline.db.repository import MatterRepository
from pipeline.models import DocType
from pipeline.storage import LocalStorage

from tests.conftest import requires_db
from tests.test_ingest import PLAINT_TEXT, ORDER_TEXT, make_scan_pdf, make_text_pdf


pytestmark = requires_db


class FakeEmbedder:
    """Deterministic 1024-dim vectors: encodes which query words are present,
    so cosine ordering is predictable without a model or an API key."""

    dim = 1024
    VOCAB = ["possession", "sale", "deed", "injunction", "notice", "status", "quo"]

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        v = [0.0] * 1024
        for i, word in enumerate(self.VOCAB):
            if word in low:
                v[i] = 1.0
        if not any(v):
            v[len(self.VOCAB)] = 1.0  # non-zero: cosine distance is undefined for 0-vectors
        return v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _add(repo: MatterRepository, tmp_path: Path, matter_id: str, name: str, body: str) -> None:
    pdf = tmp_path / name
    make_text_pdf(pdf, [body])
    repo.add_pdf(matter_id, name, pdf.read_bytes())


def test_matter_lifecycle_and_cascade(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("Sharma v. Verma", today=date(2026, 7, 15))
    _add(repo, tmp_path, m.matter_id, "plaint.pdf", PLAINT_TEXT)

    loaded = repo.get(m.matter_id)
    assert loaded.title == "Sharma v. Verma"
    assert loaded.documents[0].doc_type == DocType.PLAINT
    assert repo.matter_chunks(m.matter_id)

    repo.delete(m.matter_id)
    with pytest.raises(FileNotFoundError):
        repo.get(m.matter_id)
    # Cascade: nothing orphaned behind the matter (DPDP hard delete).
    with session_scope() as s:
        assert s.query(ChunkRow).count() == 0
        assert s.query(DocumentRow).count() == 0


def test_chunks_carry_provenance(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    _add(repo, tmp_path, m.matter_id, "plaint.pdf", PLAINT_TEXT)
    chunks = repo.matter_chunks(m.matter_id)
    assert all(c.location.file == "plaint.pdf" for c in chunks)
    # PLAINT_TEXT's only numbered para is 1; the caption block above it is
    # citable to the page but carries no para number.
    assert any(c.location.para == 1 and "sale deed" in c.text for c in chunks)
    assert any(c.location.para is None and "IN THE COURT" in c.text for c in chunks)
    assert all(c.location.page == 1 for c in chunks)


def test_reupload_replaces_document_and_its_chunks(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    _add(repo, tmp_path, m.matter_id, "doc.pdf", PLAINT_TEXT)
    first = len(repo.matter_chunks(m.matter_id))
    _add(repo, tmp_path, m.matter_id, "doc.pdf", ORDER_TEXT)  # same filename

    assert len(repo.get(m.matter_id).documents) == 1  # not duplicated
    chunks = repo.matter_chunks(m.matter_id)
    assert first > 0 and all("status quo" in c.text or "ORDER" in c.text for c in chunks)


def test_remove_document(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    _add(repo, tmp_path, m.matter_id, "plaint.pdf", PLAINT_TEXT)
    _add(repo, tmp_path, m.matter_id, "order.pdf", ORDER_TEXT)

    repo.remove_document(m.matter_id, "plaint.pdf")
    assert [d.file for d in repo.get(m.matter_id).documents] == ["order.pdf"]
    assert all(c.location.file == "order.pdf" for c in repo.matter_chunks(m.matter_id))
    with pytest.raises(FileNotFoundError):
        repo.remove_document(m.matter_id, "ghost.pdf")


def test_scan_is_pending_and_ocr_updates_in_place(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    rec = repo.add_pdf(m.matter_id, "scan.pdf", pdf.read_bytes())
    assert rec.ocr_status == "pending"
    assert repo.matter_chunks(m.matter_id) == []  # unread page yields no chunks

    class FakeOcr:
        def read_page(self, pdf_path, page_index):  # noqa: ANN001
            return ("A legal notice dated 5th June 2019 was served on the defendant.", 0.83)

    rec = repo.ocr_document(m.matter_id, "scan.pdf", FakeOcr())
    assert rec.ocr_status == "done"
    assert repo.get(m.matter_id).documents[0].ocr_status == "done"
    chunks = repo.matter_chunks(m.matter_id)
    assert chunks and "legal notice" in chunks[0].text


def test_ocr_failure_recorded(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    repo.add_pdf(m.matter_id, "scan.pdf", pdf.read_bytes())

    class BrokenOcr:
        def read_page(self, pdf_path, page_index):  # noqa: ANN001
            raise RuntimeError("engine exploded")

    with pytest.raises(RuntimeError):
        repo.ocr_document(m.matter_id, "scan.pdf", BrokenOcr())
    doc = repo.get(m.matter_id).documents[0]
    assert doc.ocr_status == "failed" and "exploded" in (doc.ocr_error or "")


def test_unreadable_pdf_leaves_nothing_behind(repo: MatterRepository, tmp_path: Path) -> None:
    from pipeline.ingest.extract import UnreadablePdf

    m = repo.create("M", today=date(2026, 7, 15))
    with pytest.raises(UnreadablePdf):
        repo.add_pdf(m.matter_id, "broken.pdf", b"%PDF-1.4 not a pdf")
    assert repo.get(m.matter_id).documents == []
    with pytest.raises(Exception):
        repo.file_bytes(m.matter_id, "broken.pdf")  # blob removed too


def test_semantic_search_uses_pgvector(tmp_path: Path, repo: MatterRepository) -> None:
    embedded = MatterRepository(
        storage=LocalStorage(tmp_path / "blobs2"), embedder=FakeEmbedder()
    )
    m = embedded.create("M", today=date(2026, 7, 15))
    _add(embedded, tmp_path, m.matter_id, "plaint.pdf", PLAINT_TEXT)
    _add(embedded, tmp_path, m.matter_id, "order.pdf", ORDER_TEXT)

    with session_scope() as s:
        assert s.query(ChunkRow).filter(ChunkRow.embedding.is_not(None)).count() > 0

    hits = embedded.search(m.matter_id, "status quo injunction", k=3)
    assert hits and any("status quo" in h.text for h in hits)


def test_search_is_scoped_to_one_matter(tmp_path: Path, repo: MatterRepository) -> None:
    """A lawyer must never retrieve another matter's text — this is a security
    boundary, not a relevance nicety."""
    embedded = MatterRepository(
        storage=LocalStorage(tmp_path / "blobs3"), embedder=FakeEmbedder()
    )
    mine = embedded.create("Mine", today=date(2026, 7, 15))
    theirs = embedded.create("Theirs", today=date(2026, 7, 15))
    _add(embedded, tmp_path, mine.matter_id, "a.pdf", PLAINT_TEXT)
    _add(embedded, tmp_path, theirs.matter_id, "b.pdf", ORDER_TEXT)

    hits = embedded.search(mine.matter_id, "status quo injunction possession", k=10)
    assert hits
    assert all(h.matter_id == mine.matter_id for h in hits)
    assert all(h.location.file == "a.pdf" for h in hits)


def test_search_falls_back_to_lexical_without_embeddings(
    repo: MatterRepository, tmp_path: Path
) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    _add(repo, tmp_path, m.matter_id, "plaint.pdf", PLAINT_TEXT)
    hits = repo.search(m.matter_id, "when was the sale deed executed", k=3)
    assert hits and "sale deed" in hits[0].text


def test_artifacts_and_drafts_roundtrip(repo: MatterRepository, tmp_path: Path) -> None:
    m = repo.create("M", today=date(2026, 7, 15))
    assert repo.load_artifacts(m.matter_id) is None

    repo.save_artifacts(m.matter_id, {"matter_id": m.matter_id, "chronology": [{"event": "x"}]})
    assert repo.load_artifacts(m.matter_id)["chronology"][0]["event"] == "x"
    repo.save_artifacts(m.matter_id, {"matter_id": m.matter_id, "chronology": []})  # upsert
    assert repo.load_artifacts(m.matter_id)["chronology"] == []

    did = repo.save_draft(
        m.matter_id, "legal_notice", {"title": "LEGAL NOTICE", "paragraphs": [{}], "missing_info": ["x"]}
    )
    assert repo.load_draft(m.matter_id, did)["title"] == "LEGAL NOTICE"
    listing = repo.list_drafts(m.matter_id)
    assert listing[0]["draft_id"] == did and listing[0]["missing_info"] == 1


def test_ocr_status_is_derived_from_pages_not_a_label(repo: MatterRepository, tmp_path: Path) -> None:
    """'done' and 'not_needed' both mean nothing-left-to-read, but only 'done'
    tells a lawyer the text came from OCR and may carry OCR error."""
    m = repo.create("M", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    key = "matters/%s/scan.pdf" % m.matter_id
    repo.storage.put(key, pdf.read_bytes())

    from pipeline.ingest.extract import PageExtract
    from pipeline.models import Language

    ocrd = [
        PageExtract(page=1, text="A legal notice was served.", method="ocr",
                    confidence=0.6, language=Language.ENGLISH)
    ]
    rec = repo._persist_document(m.matter_id, "scan.pdf", key, ocrd)
    assert rec.ocr_status == "done"  # not "not_needed"

    digital = [
        PageExtract(page=1, text="A legal notice was served.", method="text_layer",
                    confidence=1.0, language=Language.ENGLISH)
    ]
    rec = repo._persist_document(m.matter_id, "digital.pdf", key, digital)
    assert rec.ocr_status == "not_needed"
