"""Structuring tests: chunking, date extraction, lexical index."""

from datetime import date

from pipeline.ingest.extract import PageExtract
from pipeline.models import DocType, Language
from pipeline.structure import LexicalChunkIndex, chunk_pages, extract_dates

NUMBERED_PAGE = (
    "IN THE COURT OF THE CIVIL JUDGE, DELHI\n"
    "PLAINT\n\n"
    "1. The plaintiff is a resident of Delhi and owner of the suit property "
    "bearing no. 42, Green Park.\n"
    "2. A sale deed for the suit property was executed on 12.03.2019 between "
    "the plaintiff and defendant no. 1.\n"
    "3. Defendant no. 1 failed to hand over vacant possession despite the "
    "notice dated 5th June, 2019.\n"
)


def page(text: str, number: int = 1, method: str = "text_layer") -> PageExtract:
    return PageExtract(
        page=number,
        text=text,
        method=method,  # type: ignore[arg-type]
        confidence=1.0 if method == "text_layer" else 0.0,
        language=Language.ENGLISH,
    )


# -- chunking -------------------------------------------------------------------


def test_chunks_follow_paragraph_numbering() -> None:
    chunks = chunk_pages("m1", "plaint.pdf", DocType.PLAINT, [page(NUMBERED_PAGE)])
    paras = [c.location.para for c in chunks]
    assert paras == [None, 1, 2, 3]  # caption block, then numbered paras
    assert chunks[0].location.file == "plaint.pdf"
    assert chunks[0].location.page == 1
    assert "sale deed" in chunks[2].text


def test_unnumbered_page_falls_back_to_blank_line_paragraphs() -> None:
    text = "ORDER\n\nParties shall maintain status quo over the suit property.\n\nListed on 14.08.2019 for arguments."
    chunks = chunk_pages("m1", "order.pdf", DocType.ORDER, [page(text)])
    assert len(chunks) == 2
    assert all(c.location.para is None for c in chunks)


def test_needs_ocr_pages_produce_no_chunks() -> None:
    chunks = chunk_pages(
        "m1", "scan.pdf", DocType.OTHER, [page("", number=1, method="needs_ocr")]
    )
    assert chunks == []  # an unread page can never support a claim


# -- dates ----------------------------------------------------------------------


def test_extract_dates_indian_formats() -> None:
    mentions = extract_dates(NUMBERED_PAGE)
    values = [m.value for m in mentions]
    assert date(2019, 3, 12) in values  # 12.03.2019, day-first
    assert date(2019, 6, 5) in values  # 5th June, 2019


def test_extract_dates_more_formats() -> None:
    text = "Hearing on 14/08/2019, adjourned to 1-09-2019. Filed March 3, 2020."
    values = [m.value for m in extract_dates(text)]
    assert values == [date(2019, 8, 14), date(2019, 9, 1), date(2020, 3, 3)]


def test_invalid_dates_are_skipped_not_guessed() -> None:
    assert extract_dates("dated 32.13.2019 and 00/00/2019") == []


# -- index ----------------------------------------------------------------------


def test_lexical_index_ranks_relevant_chunk_first() -> None:
    chunks = chunk_pages("m1", "plaint.pdf", DocType.PLAINT, [page(NUMBERED_PAGE)])
    index = LexicalChunkIndex()
    index.add(chunks)
    top = index.search("when was the sale deed executed", k=2)
    assert top and "sale deed" in top[0].text


def test_lexical_index_empty_query_returns_nothing() -> None:
    index = LexicalChunkIndex()
    assert index.search("the and was") == []
