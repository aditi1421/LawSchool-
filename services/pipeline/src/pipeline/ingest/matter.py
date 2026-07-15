"""Matter record types.

These are the API contract for a matter and its documents: the repository
(pipeline.db.repository) returns them, and FastAPI serialises them.

The filesystem MatterStore that used to live here has been retired — matters
are in Postgres and case files in object storage. Keeping records on a laptop
was never shippable for privileged material.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.models import DocType, Language


OcrStatus = Literal["not_needed", "pending", "running", "done", "failed"]


class PageRecord(BaseModel):
    page: int
    method: str
    confidence: float
    language: Language
    chars: int


class DocumentRecord(BaseModel):
    file: str  # filename within the matter
    doc_type: DocType
    pages: list[PageRecord]
    needs_ocr_pages: list[int] = Field(default_factory=list)
    # OCR is slow (seconds per page on CPU), so it never runs inside the upload
    # request — see MatterRepository.add_pdf / ocr_document.
    ocr_status: OcrStatus = "not_needed"
    ocr_error: str | None = None


class MatterManifest(BaseModel):
    matter_id: str
    title: str
    created: date
    documents: list[DocumentRecord] = Field(default_factory=list)
