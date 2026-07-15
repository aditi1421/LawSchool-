"""Ingestion tests over synthetic PDFs built with PyMuPDF."""

from datetime import date
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app
from pipeline.ingest import MatterStore, classify_doc_type, extract_pages
from pipeline.ingest.extract import detect_language
from pipeline.models import DocType, Language

PLAINT_TEXT = (
    "IN THE COURT OF THE CIVIL JUDGE, DELHI\n"
    "Suit No. 123 of 2019\n\n"
    "PLAINT\n\n"
    "Suit for possession and permanent injunction.\n"
    "1. The plaintiff states that a sale deed for the suit property was "
    "executed on 12 March 2019 between the plaintiff and defendant no. 1."
)

ORDER_TEXT = (
    "ORDER\n\n"
    "It is ordered that the parties shall maintain status quo over the suit "
    "property until the next date of hearing, i.e. 14 August 2019."
)


def make_text_pdf(path: Path, pages: list[str]) -> None:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()


def make_scan_pdf(path: Path) -> None:
    """An image-only page: no text layer at all (simulates a scan)."""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100))
    pix.clear_with(200)
    page.insert_image(fitz.Rect(72, 72, 272, 172), pixmap=pix)
    doc.save(path)
    doc.close()


# -- extraction ---------------------------------------------------------------


def test_text_layer_extraction(tmp_path: Path) -> None:
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT, ORDER_TEXT])
    pages = extract_pages(pdf)
    assert len(pages) == 2
    assert pages[0].method == "text_layer"
    assert pages[0].confidence == 1.0
    assert pages[0].page == 1
    assert "sale deed" in pages[0].text
    assert pages[0].language == Language.ENGLISH


def test_scan_without_ocr_is_flagged_never_guessed(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    pages = extract_pages(pdf, ocr=None)
    assert pages[0].method == "needs_ocr"
    assert pages[0].confidence == 0.0
    assert pages[0].text == ""


def test_hindi_language_detection() -> None:
    # Tested on raw text: PyMuPDF's base-14 fonts cannot embed Devanagari, so a
    # synthetic PDF roundtrip is meaningless here. Real Hindi filings carry
    # embedded fonts and extract as proper Devanagari text.
    hindi = "वाद पत्र — वादी का कथन है कि विक्रय विलेख निष्पादित किया गया था"
    assert detect_language(hindi) == Language.HINDI
    assert detect_language(PLAINT_TEXT) == Language.ENGLISH
    mixed = "The plaintiff filed the suit. वादी ने वाद दायर किया। " + "और " * 30
    assert detect_language(mixed) == Language.HINDI


# -- classification ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PLAINT_TEXT, DocType.PLAINT),
        (ORDER_TEXT, DocType.ORDER),
        ("FIRST INFORMATION REPORT\nP.S. Hauz Khas\nu/s 420 IPC", DocType.FIR),
        ("CHARGE SHEET\nFinal report under section 173 CrPC in FIR 45/2020", DocType.CHARGESHEET),
        ("BAIL APPLICATION under section 439 CrPC", DocType.BAIL_APPLICATION),
        ("WRITTEN STATEMENT on behalf of defendant no. 1", DocType.WRITTEN_STATEMENT),
        ("AFFIDAVIT\nI solemnly affirm that the contents are true", DocType.AFFIDAVIT),
        ("random unrelated text about the weather", DocType.OTHER),
    ],
)
def test_classifier(text: str, expected: DocType) -> None:
    assert classify_doc_type(text) == expected


def test_chargesheet_wins_over_fir_mention() -> None:
    # A chargesheet references its FIR; it must still classify as chargesheet.
    text = "CHARGE SHEET\nIn the matter of First Information Report No. 45/2020"
    assert classify_doc_type(text) == DocType.CHARGESHEET


def test_order_mentioning_written_statement_is_still_an_order() -> None:
    # Regression: found by live smoke test — an order granting time to file a
    # written statement was classified as the written statement itself.
    text = (
        "IN THE COURT OF THE CIVIL JUDGE, SAKET\nSuit No. 482 of 2023\nORDER\n\n"
        "Written statement on behalf of defendant no. 1 has not been filed despite "
        "opportunity. Last opportunity of two weeks is granted."
    )
    assert classify_doc_type(text) == DocType.ORDER

    # Same document as PDF-extracted text: the body sentence wraps onto its own
    # line ("Written statement on behalf of defendant no. 1 has not been filed
    # despite\n"), which looks heading-like. The real heading appears earlier.
    wrapped = (
        "IN THE COURT OF THE CIVIL JUDGE (SENIOR DIVISION), SAKET, NEW DELHI\n"
        "Suit No. 482 of 2023\nORDER\n"
        "Present: Counsel for the plaintiff. Counsel for defendant no. 1.\n"
        "Written statement on behalf of defendant no. 1 has not been filed despite\n"
        "opportunity. Last opportunity of two weeks is granted."
    )
    assert classify_doc_type(wrapped) == DocType.ORDER

    # A real written statement still classifies as one.
    ws = "WRITTEN STATEMENT ON BEHALF OF DEFENDANT NO. 1\n\n1. The suit is not maintainable."
    assert classify_doc_type(ws) == DocType.WRITTEN_STATEMENT


# -- matter store ---------------------------------------------------------------


def test_matter_lifecycle(tmp_path: Path) -> None:
    store = MatterStore(tmp_path / "matters")
    manifest = store.create("Sharma v. Verma", today=date(2026, 7, 15))

    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    record = store.add_pdf(manifest.matter_id, "plaint.pdf", pdf.read_bytes())

    assert record.doc_type == DocType.PLAINT
    assert record.pages[0].method == "text_layer"

    loaded = store.get(manifest.matter_id)
    assert loaded.documents[0].file == "plaint.pdf"
    pages = store.load_pages(manifest.matter_id, "plaint.pdf")
    assert "sale deed" in pages[0].text  # provenance text roundtrips

    store.delete(manifest.matter_id)
    assert store.list_matters() == []  # hard delete removes everything


# -- api -------------------------------------------------------------------------


def test_scanned_upload_is_pending_not_ocrd_inline(tmp_path: Path) -> None:
    # Regression: OCR used to run inside add_pdf on the request path, which
    # blocked the API event loop for minutes on a scanned file.
    store = MatterStore(tmp_path / "matters")
    manifest = store.create("Scan", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)

    record = store.add_pdf(manifest.matter_id, "scan.pdf", pdf.read_bytes())
    assert record.ocr_status == "pending"
    assert record.needs_ocr_pages == [1]
    assert record.pages[0].method == "needs_ocr"


def test_ocr_document_updates_status_and_pages(tmp_path: Path) -> None:
    store = MatterStore(tmp_path / "matters")
    manifest = store.create("Scan", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    store.add_pdf(manifest.matter_id, "scan.pdf", pdf.read_bytes())

    class FakeOcr:
        def read_page(self, pdf_path: Path, page_index: int) -> tuple[str, float]:
            return ("A legal notice dated 5th June 2019 was served.", 0.82)

    record = store.ocr_document(manifest.matter_id, "scan.pdf", FakeOcr())
    assert record.ocr_status == "done"
    assert record.needs_ocr_pages == []
    assert record.pages[0].method == "ocr"
    assert "legal notice" in store.load_pages(manifest.matter_id, "scan.pdf")[0].text
    # status survives a reload
    assert store.get(manifest.matter_id).documents[0].ocr_status == "done"


def test_ocr_failure_is_recorded_not_swallowed(tmp_path: Path) -> None:
    store = MatterStore(tmp_path / "matters")
    manifest = store.create("Scan", today=date(2026, 7, 15))
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    store.add_pdf(manifest.matter_id, "scan.pdf", pdf.read_bytes())

    class BrokenOcr:
        def read_page(self, pdf_path: Path, page_index: int) -> tuple[str, float]:
            raise RuntimeError("engine exploded")

    with pytest.raises(RuntimeError):
        store.ocr_document(manifest.matter_id, "scan.pdf", BrokenOcr())
    doc = store.get(manifest.matter_id).documents[0]
    assert doc.ocr_status == "failed"
    assert doc.ocr_error is not None and "exploded" in doc.ocr_error


def test_upload_of_scan_returns_fast_and_queues_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload response must not wait on OCR."""
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "pipeline.api._ocr_worker",
        lambda data_dir, matter_id, filename: calls.append((matter_id, filename)),
    )
    client = TestClient(app)
    matter_id = client.post("/matters", json={"title": "Scan"}).json()["matter_id"]

    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    resp = client.post(
        f"/matters/{matter_id}/files",
        files={"file": ("scan.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200
    assert resp.json()["ocr_status"] == "pending"
    assert calls == [(matter_id, "scan.pdf")]  # queued, not run inline


def test_text_layer_upload_does_not_queue_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))
    calls: list[str] = []
    monkeypatch.setattr(
        "pipeline.api._ocr_worker",
        lambda data_dir, matter_id, filename: calls.append(filename),
    )
    client = TestClient(app)
    matter_id = client.post("/matters", json={"title": "Digital"}).json()["matter_id"]

    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    resp = client.post(
        f"/matters/{matter_id}/files",
        files={"file": ("plaint.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert resp.json()["ocr_status"] == "not_needed"
    assert calls == []


def test_start_ocr_endpoint_queues_and_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))
    monkeypatch.setattr("pipeline.api._ocr_worker", lambda *a: None)
    client = TestClient(app)
    matter_id = client.post("/matters", json={"title": "Scan"}).json()["matter_id"]
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    client.post(
        f"/matters/{matter_id}/files",
        files={"file": ("scan.pdf", pdf.read_bytes(), "application/pdf")},
    )

    resp = client.post(f"/matters/{matter_id}/files/scan.pdf/ocr")
    assert resp.status_code == 202
    assert resp.json()["ocr_status"] == "pending"
    assert client.post(f"/matters/{matter_id}/files/nope.pdf/ocr").status_code == 404


def test_corrupt_pdf_gives_a_clear_422_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: an unreadable PDF raised through the handler, producing a 500
    # generated outside the CORS middleware — the browser then reported the
    # server error as an opaque network failure.
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))
    client = TestClient(app)
    matter_id = client.post("/matters", json={"title": "Bad"}).json()["matter_id"]

    resp = client.post(
        "/matters/%s/files" % matter_id,
        files={"file": ("broken.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")},
    )
    assert resp.status_code == 422
    assert "corrupted" in resp.json()["detail"].lower()
    # The unreadable file is not left behind in the matter.
    assert client.get(f"/matters/{matter_id}").json()["documents"] == []


def test_unhandled_errors_carry_cors_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 500 must reach the browser as JSON with CORS headers, not as an
    opaque 'Failed to fetch'."""
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("pipeline.api.get_store", boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/matters", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 500
    assert "kaboom" in resp.json()["detail"]
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_password_protected_pdf_is_reported(tmp_path: Path) -> None:
    pdf = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret", fontsize=11)
    doc.save(pdf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="hunter2")
    doc.close()

    from pipeline.ingest.extract import UnreadablePdf

    with pytest.raises(UnreadablePdf, match="password-protected"):
        extract_pages(pdf)


def test_api_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWSCHOOL_DATA_DIR", str(tmp_path / "matters"))
    client = TestClient(app)

    created = client.post("/matters", json={"title": "Sharma v. Verma"}).json()
    matter_id = created["matter_id"]

    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    resp = client.post(
        f"/matters/{matter_id}/files",
        files={"file": ("plaint.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200
    assert resp.json()["doc_type"] == "plaint"

    assert client.get(f"/matters/{matter_id}").json()["documents"][0]["file"] == "plaint.pdf"
    assert client.post(f"/matters/{matter_id}/files", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 422
    assert client.delete(f"/matters/{matter_id}").status_code == 204
    assert client.get(f"/matters/{matter_id}").status_code == 404
