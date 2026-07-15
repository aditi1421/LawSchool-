"""Chunking by legal paragraph numbering.

Indian pleadings and orders are paragraph-numbered ("1.", "2.", ... often
resetting per document). We split on numbered paragraphs so a chunk maps to
a citable para; unnumbered text between them attaches to the preceding chunk.
Pages without any numbered paras fall back to blank-line paragraphs with
para=None (citable to the page).
"""

import re

from pipeline.ingest.extract import PageExtract
from pipeline.models import Chunk, Citation, DocType

# A numbered legal para starts a line: "12." / "12)" / "(12)" followed by text.
_PARA_START = re.compile(r"^\s*\(?(\d{1,3})[.)]\s+", re.MULTILINE)

MIN_CHUNK_CHARS = 15  # ignore stray fragments (page numbers, stamps)


def _page_chunks(text: str) -> list[tuple[int | None, str]]:
    """Split one page's text into (para_number, text) pieces."""
    matches = list(_PARA_START.finditer(text))
    if not matches:
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return [(None, p) for p in parts if len(p) >= MIN_CHUNK_CHARS]

    pieces: list[tuple[int | None, str]] = []
    head = text[: matches[0].start()].strip()
    if len(head) >= MIN_CHUNK_CHARS:
        pieces.append((None, head))  # heading / caption before first numbered para
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        if len(body) >= MIN_CHUNK_CHARS:
            pieces.append((int(m.group(1)), body))
    return pieces


def chunk_pages(
    matter_id: str, filename: str, doc_type: DocType, pages: list[PageExtract]
) -> list[Chunk]:
    """Chunk a document's extracted pages, preserving {file, page, para} provenance.

    Pages flagged `needs_ocr` produce no chunks — an unread page can never
    silently support a claim.
    """
    chunks: list[Chunk] = []
    for page in pages:
        if page.method == "needs_ocr":
            continue
        for para, text in _page_chunks(page.text):
            chunks.append(
                Chunk(
                    matter_id=matter_id,
                    location=Citation(file=filename, page=page.page, para=para),
                    text=text,
                    doc_type=doc_type,
                    language=page.language,
                    ocr_confidence=page.confidence,
                )
            )
    return chunks
