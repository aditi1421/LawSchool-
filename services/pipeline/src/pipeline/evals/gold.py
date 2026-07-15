"""Gold annotation schema.

One GoldMatter per annotated case file. Source PDFs live in
evals/gold/files/ (never committed); annotations are JSON files in
evals/gold/annotations/ (committed), one per matter.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.models import Citation, DocType, Language


class GoldEvent(BaseModel):
    """A gold-standard chronology event with its source location."""

    event_date: date | None = None
    description: str
    source: Citation


class GoldFact(BaseModel):
    """A key fact the pipeline must recover (party name, section, relief...)."""

    key: str
    value: str
    source: Citation


class GoldDocEntry(BaseModel):
    file: str
    title: str
    doc_type: DocType
    language: Language


class GoldMatter(BaseModel):
    """Hand-annotated ground truth for one matter."""

    matter_id: str
    # Descriptive metadata only — generation is matter-agnostic.
    lens: Literal["civil", "criminal"] | None = None
    files: list[str] = Field(min_length=1)  # filenames under evals/gold/files/<matter_id>/
    events: list[GoldEvent] = Field(default_factory=list)
    facts: list[GoldFact] = Field(default_factory=list)
    doc_index: list[GoldDocEntry] = Field(default_factory=list)
    notes: str = ""  # annotator notes: ambiguities, known conflicts in the record
