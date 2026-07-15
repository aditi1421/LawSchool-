"""Extraction and classification tests over synthetic PDFs built with PyMuPDF.

Pure — no database. Matter storage lives in test_repository.py and the HTTP
surface in test_api.py.
"""

from pathlib import Path

import fitz
import pytest
from pipeline.ingest import classify_doc_type, extract_pages
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
