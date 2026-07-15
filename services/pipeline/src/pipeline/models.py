"""Core typed data model.

Central invariant: an artifact row without provenance is unrepresentable.
Every factual claim carries at least one Citation into the uploaded record,
or lives in an explicit `not_found` / `undated` / `conflicts` bucket.
Dates are never inferred; conflicts are never silently resolved.
"""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DocType(str, Enum):
    PLAINT = "plaint"
    WRITTEN_STATEMENT = "written_statement"
    REPLICATION = "replication"
    ORDER = "order"
    JUDGMENT = "judgment"
    AFFIDAVIT = "affidavit"
    EXHIBIT = "exhibit"
    FIR = "fir"
    CHARGESHEET = "chargesheet"
    BAIL_APPLICATION = "bail_application"
    NOTICE = "notice"
    OTHER = "other"


class Language(str, Enum):
    ENGLISH = "en"
    HINDI = "hi"


class Citation(BaseModel):
    """Pointer to a location in the uploaded record."""

    file: str
    page: int = Field(ge=1)
    para: int | None = None


class Chunk(BaseModel):
    """A provenance-carrying unit of extracted text."""

    matter_id: str
    location: Citation
    text: str
    doc_type: DocType
    language: Language
    ocr_confidence: float = Field(ge=0.0, le=1.0)


class ChronologyEvent(BaseModel):
    """A dated (or explicitly undated) event in the matter."""

    event_date: date | None = None  # None => undated bucket; never inferred
    event: str
    actor: str | None = None
    cites: list[Citation] = Field(min_length=1)
    confidence: Literal["high", "low_ocr"] = "high"


class OrderEntry(BaseModel):
    """One entry in the chronology of proceedings."""

    order_date: date | None = None
    court: str | None = None
    direction: str
    next_date: date | None = None
    cites: list[Citation] = Field(min_length=1)


class SidePosition(BaseModel):
    position: str
    cites: list[Citation] = Field(min_length=1)


class Contention(BaseModel):
    """Rival positions on one issue, side by side."""

    issue: str
    petitioner: SidePosition | None = None
    respondent: SidePosition | None = None

    @model_validator(mode="after")
    def at_least_one_side(self) -> "Contention":
        if self.petitioner is None and self.respondent is None:
            raise ValueError("a contention must carry at least one side's position")
        return self


class Issue(BaseModel):
    """An issue / point for determination."""

    text: str
    origin: Literal["framed_by_court", "inferred"]
    cites: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def court_framed_requires_cite(self) -> "Issue":
        if self.origin == "framed_by_court" and not self.cites:
            raise ValueError("court-framed issues must cite the framing order")
        return self


class DocIndexEntry(BaseModel):
    exhibit_no: str | None = None
    title: str
    doc_type: DocType
    doc_date: date | None = None
    pages: int = Field(ge=1)
    language: Language
    ocr_quality: Literal["good", "low"]


class Conflict(BaseModel):
    """Two or more documents disagree on a fact — surfaced, never resolved silently."""

    fact: str
    positions: list[SidePosition] = Field(min_length=2)


class MatterArtifacts(BaseModel):
    """The full hearing-ready brief for one matter."""

    matter_id: str
    chronology: list[ChronologyEvent] = Field(default_factory=list)
    proceedings: list[OrderEntry] = Field(default_factory=list)
    contentions: list[Contention] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    doc_index: list[DocIndexEntry] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)  # sought but absent from the record
