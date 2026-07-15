"""Typed draft documents.

Same honesty contract as artifacts, adapted for creative output:
- A paragraph asserting facts from the record carries citations into it.
- Information the record does not contain appears as an explicit
  ``[● description]`` placeholder and is listed in ``missing_info`` —
  never invented.
- Boilerplate (prayer language, verification clauses, formal parts) is
  marked as such; it needs no citations.
- ``verified`` is set by code (pipeline.drafting.generate.validate_draft),
  never by the model.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.models import Citation


class DraftType(str, Enum):
    LEGAL_NOTICE = "legal_notice"
    WRITTEN_STATEMENT = "written_statement"
    BAIL_APPLICATION = "bail_application"
    PLAINT = "plaint"


class DraftParagraph(BaseModel):
    text: str
    kind: Literal["factual", "boilerplate"] = "factual"
    cites: list[Citation] = Field(default_factory=list)
    # Set by validate_draft: True when every cite resolves to the record and
    # (for factual paragraphs) at least one cite or placeholder is present.
    verified: bool = False


class DraftDocument(BaseModel):
    matter_id: str
    doc_type: DraftType
    title: str  # e.g. "LEGAL NOTICE" / "WRITTEN STATEMENT ON BEHALF OF DEFENDANT NO. 1"
    court_header: str | None = None  # cause title block, when applicable
    paragraphs: list[DraftParagraph] = Field(default_factory=list)
    prayer: list[str] = Field(default_factory=list)  # reliefs / demands, numbered
    missing_info: list[str] = Field(default_factory=list)  # what the record lacks
