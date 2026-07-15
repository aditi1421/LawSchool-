"""Typed draft documents.

Same honesty contract as artifacts, adapted for creative output:
- A paragraph asserting facts from the record carries citations into it.
- Information the record does not contain appears as an explicit
  ``[● description]`` placeholder and is listed in ``missing_info`` —
  never invented.
- Boilerplate (prayer language, verification clauses, formal parts) is
  marked as such; it needs no citations.
- ``verified`` is set by code (pipeline.drafting.verify.validate_draft),
  never by the model.
- ``list_of_dates`` is DERIVED, never drafted: it is a rendering of the
  matter's fidelity-checked chronology (see generate.derive_list_of_dates),
  and whatever a model puts there is discarded. Re-deriving a chronology in
  prose could contradict the verified one — worse than either being wrong
  alone.
"""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.models import Citation


class DraftType(str, Enum):
    LEGAL_NOTICE = "legal_notice"
    WRITTEN_STATEMENT = "written_statement"
    BAIL_APPLICATION = "bail_application"
    PLAINT = "plaint"
    # Paperbook components / petitions:
    SYNOPSIS_LOD = "synopsis_and_list_of_dates"
    WRIT_PETITION = "writ_petition"  # Article 226
    SLP = "slp"  # special leave petition, Article 136


# Draft types assembled FROM the verified chronology (MatterArtifacts must
# exist before these can be drafted).
COMPOSED_TYPES = frozenset({DraftType.SYNOPSIS_LOD, DraftType.SLP})


class DraftParagraph(BaseModel):
    text: str
    # factual: asserts facts of this matter — must cite or carry a placeholder.
    # ground: a ground of challenge (petitions) — same rules as factual, but
    #         lettered (A, B, ...) instead of numbered in export.
    # heading: a section heading (SYNOPSIS, QUESTIONS OF LAW, GROUNDS ...) —
    #          asserts nothing, needs nothing.
    # boilerplate: formal parts, prayer language, verification clauses.
    kind: Literal["factual", "boilerplate", "heading", "ground"] = "factual"
    cites: list[Citation] = Field(default_factory=list)
    # Set by validate_draft: True when every cite resolves to the record and
    # (for factual/ground paragraphs) at least one cite or placeholder is
    # present.
    verified: bool = False


class ListOfDatesEntry(BaseModel):
    """One row of a List of Dates & Events — a rendering of a verified
    ChronologyEvent, so the same contract: cited, and the date is the
    record's own or explicitly absent."""

    event_date: date | None = None  # None => undated bucket; never inferred
    event: str
    cites: list[Citation] = Field(min_length=1)
    confidence: Literal["high", "low_ocr"] = "high"


class DraftDocument(BaseModel):
    matter_id: str
    doc_type: DraftType
    title: str  # e.g. "LEGAL NOTICE" / "WRITTEN STATEMENT ON BEHALF OF DEFENDANT NO. 1"
    court_header: str | None = None  # cause title block, when applicable
    # Paperbook front matter (synopsis_and_list_of_dates, slp); empty otherwise.
    synopsis: list[DraftParagraph] = Field(default_factory=list)
    list_of_dates: list[ListOfDatesEntry] = Field(default_factory=list)
    paragraphs: list[DraftParagraph] = Field(default_factory=list)
    prayer: list[str] = Field(default_factory=list)  # reliefs / demands, numbered
    missing_info: list[str] = Field(default_factory=list)  # what the record lacks

    def body_paragraphs(self) -> list[tuple[str, int, DraftParagraph]]:
        """Every prose paragraph with its address — ('synopsis'|'paragraphs',
        index, paragraph). The address names a location in THIS document so a
        verification failure can point the model (and the reader) at the exact
        paragraph."""
        return [("synopsis", i, p) for i, p in enumerate(self.synopsis)] + [
            ("paragraphs", i, p) for i, p in enumerate(self.paragraphs)
        ]
