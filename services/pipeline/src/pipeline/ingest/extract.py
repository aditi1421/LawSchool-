"""Page-level text extraction with provenance and OCR fallback.

Strategy per page:
1. PyMuPDF text layer (digital-native PDFs) — confidence 1.0.
2. No usable text layer -> OCR engine (EasyOCR when installed) with its
   reported confidence.
3. No OCR available -> page flagged `needs_ocr` with confidence 0.0 and empty
   text. Honesty rule: a flagged page is never silently used to support a claim.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import fitz  # PyMuPDF

from pipeline.models import Language

# A page with fewer characters than this is treated as having no text layer
# (scans often carry a few stray watermark/stamp characters).
MIN_TEXT_LAYER_CHARS = 20


@dataclass
class PageExtract:
    page: int  # 1-indexed
    text: str
    method: Literal["text_layer", "ocr", "needs_ocr"]
    confidence: float
    language: Language


class OcrEngine(Protocol):
    def read_page(self, pdf_path: Path, page_index: int) -> tuple[str, float]:
        """Return (text, mean confidence in [0,1]) for a 0-indexed page."""
        ...


def detect_language(text: str) -> Language:
    """Script-based detection: Devanagari-dominant text is Hindi, else English."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return Language.ENGLISH
    devanagari = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    return Language.HINDI if devanagari / len(letters) > 0.3 else Language.ENGLISH


class EasyOcrEngine:
    """EasyOCR-backed engine (optional `ocr` extra). Lazily imports easyocr."""

    def __init__(self) -> None:
        import easyocr  # heavy import; only when OCR is actually used

        self._reader = easyocr.Reader(["en", "hi"], gpu=False)

    def read_page(self, pdf_path: Path, page_index: int) -> tuple[str, float]:
        with fitz.open(pdf_path) as doc:
            pix = doc[page_index].get_pixmap(dpi=200)
            image_bytes = pix.tobytes("png")
        results = self._reader.readtext(image_bytes, detail=1)
        if not results:
            return "", 0.0
        text = " ".join(r[1] for r in results)
        confidence = sum(float(r[2]) for r in results) / len(results)
        return text, confidence


def default_ocr_engine() -> OcrEngine | None:
    try:
        return EasyOcrEngine()
    except ImportError:
        return None


class UnreadablePdf(ValueError):
    """The file could not be opened as a PDF (corrupt, truncated, or encrypted)."""


def extract_pages(pdf_path: Path, ocr: OcrEngine | None = None) -> list[PageExtract]:
    """Extract every page of a PDF with method + confidence + language.

    Raises UnreadablePdf with a human-readable reason when the file cannot be
    opened — callers surface this to the user rather than 500-ing.
    """
    pages: list[PageExtract] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise UnreadablePdf(
            f"Could not open {pdf_path.name} as a PDF — the file may be corrupted "
            f"or incomplete."
        ) from exc
    with doc:
        if doc.needs_pass:
            raise UnreadablePdf(
                f"{pdf_path.name} is password-protected. Remove the password and "
                f"upload again."
            )
        page_count = doc.page_count
        if page_count == 0:
            raise UnreadablePdf(f"{pdf_path.name} contains no pages.")
        texts = [doc[i].get_text().strip() for i in range(page_count)]

    for i, text in enumerate(texts):
        if len(text) >= MIN_TEXT_LAYER_CHARS:
            pages.append(
                PageExtract(
                    page=i + 1,
                    text=text,
                    method="text_layer",
                    confidence=1.0,
                    language=detect_language(text),
                )
            )
            continue
        if ocr is not None:
            ocr_text, ocr_conf = ocr.read_page(pdf_path, i)
            pages.append(
                PageExtract(
                    page=i + 1,
                    text=ocr_text,
                    method="ocr",
                    confidence=ocr_conf,
                    language=detect_language(ocr_text),
                )
            )
            continue
        pages.append(
            PageExtract(
                page=i + 1,
                text="",
                method="needs_ocr",
                confidence=0.0,
                language=Language.ENGLISH,
            )
        )
    return pages
