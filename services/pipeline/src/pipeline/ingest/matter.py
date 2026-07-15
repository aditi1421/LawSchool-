"""Matter storage: files on disk + a JSON manifest per matter.

Dev-friendly local storage under a root directory (gitignored `data/`);
the same interface later fronts object storage + Postgres. Hard delete is a
first-class operation (DPDP: "delete my matter" removes everything).
"""

import json
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import OcrEngine, PageExtract, extract_pages
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
    # request — see MatterStore.add_pdf / ocr_document.
    ocr_status: OcrStatus = "not_needed"
    ocr_error: str | None = None


class MatterManifest(BaseModel):
    matter_id: str
    title: str
    created: date
    documents: list[DocumentRecord] = Field(default_factory=list)


class MatterStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    def _matter_dir(self, matter_id: str) -> Path:
        return self.root / matter_id

    def _manifest_path(self, matter_id: str) -> Path:
        return self._matter_dir(matter_id) / "manifest.json"

    def files_dir(self, matter_id: str) -> Path:
        return self._matter_dir(matter_id) / "files"

    def pages_path(self, matter_id: str, filename: str) -> Path:
        return self._matter_dir(matter_id) / "pages" / f"{filename}.json"

    # -- lifecycle ----------------------------------------------------------
    def create(self, title: str, today: date) -> MatterManifest:
        matter_id = uuid.uuid4().hex[:12]
        manifest = MatterManifest(matter_id=matter_id, title=title, created=today)
        self.files_dir(matter_id).mkdir(parents=True)
        (self._matter_dir(matter_id) / "pages").mkdir()
        self._save(manifest)
        return manifest

    def get(self, matter_id: str) -> MatterManifest:
        return MatterManifest.model_validate(
            json.loads(self._manifest_path(matter_id).read_text())
        )

    def list_matters(self) -> list[MatterManifest]:
        return [
            MatterManifest.model_validate(json.loads(p.read_text()))
            for p in sorted(self.root.glob("*/manifest.json"))
        ]

    def delete(self, matter_id: str) -> None:
        """Hard delete: files, extracted pages, manifest — everything."""
        shutil.rmtree(self._matter_dir(matter_id))

    # -- ingestion ----------------------------------------------------------
    def _record_for(self, filename: str, pages: list[PageExtract]) -> DocumentRecord:
        head_text = "\n".join(p.text for p in pages[:3])
        needs_ocr = [p.page for p in pages if p.method == "needs_ocr"]
        return DocumentRecord(
            file=filename,
            doc_type=classify_doc_type(head_text),
            pages=[
                PageRecord(
                    page=p.page,
                    method=p.method,
                    confidence=p.confidence,
                    language=p.language,
                    chars=len(p.text),
                )
                for p in pages
            ],
            needs_ocr_pages=needs_ocr,
            ocr_status="pending" if needs_ocr else "not_needed",
        )

    def _put_record(self, matter_id: str, record: DocumentRecord) -> None:
        manifest = self.get(matter_id)
        manifest.documents = [d for d in manifest.documents if d.file != record.file]
        manifest.documents.append(record)
        self._save(manifest)

    def add_pdf(self, matter_id: str, filename: str, content: bytes) -> DocumentRecord:
        """Store a PDF and extract its text layer. Fast — never runs OCR.

        Pages with no text layer are flagged `needs_ocr` and the document is
        marked ocr_status="pending"; call `ocr_document` (off the request path)
        to read them.
        """
        dest = self.files_dir(matter_id) / filename
        dest.write_bytes(content)

        try:
            pages = extract_pages(dest, ocr=None)
        except Exception:
            dest.unlink(missing_ok=True)  # don't keep a file we can't read
            raise
        record = self._record_for(filename, pages)
        self._save_pages(matter_id, filename, pages)
        self._put_record(matter_id, record)
        return record

    def set_ocr_status(
        self,
        matter_id: str,
        filename: str,
        status: OcrStatus,
        error: str | None = None,
    ) -> None:
        manifest = self.get(matter_id)
        for doc in manifest.documents:
            if doc.file == filename:
                doc.ocr_status = status
                doc.ocr_error = error
        self._save(manifest)

    def ocr_document(
        self, matter_id: str, filename: str, ocr: OcrEngine
    ) -> DocumentRecord:
        """Re-extract a stored PDF with OCR. Blocking and slow — never call
        this from an async request handler; run it in a worker thread."""
        self.set_ocr_status(matter_id, filename, "running")
        try:
            path = self.files_dir(matter_id) / filename
            pages = extract_pages(path, ocr=ocr)
            record = self._record_for(filename, pages)
            record.ocr_status = "done"
            self._save_pages(matter_id, filename, pages)
            self._put_record(matter_id, record)
            return record
        except Exception as exc:
            self.set_ocr_status(matter_id, filename, "failed", str(exc))
            raise

    def load_pages(self, matter_id: str, filename: str) -> list[PageExtract]:
        raw = json.loads(self.pages_path(matter_id, filename).read_text())
        return [
            PageExtract(
                page=r["page"],
                text=r["text"],
                method=r["method"],
                confidence=r["confidence"],
                language=Language(r["language"]),
            )
            for r in raw
        ]

    # -- internals ----------------------------------------------------------
    def _save(self, manifest: MatterManifest) -> None:
        self._manifest_path(manifest.matter_id).write_text(
            manifest.model_dump_json(indent=2)
        )

    def _save_pages(self, matter_id: str, filename: str, pages: list[PageExtract]) -> None:
        payload = [
            {
                "page": p.page,
                "text": p.text,
                "method": p.method,
                "confidence": p.confidence,
                "language": p.language.value,
            }
            for p in pages
        ]
        self.pages_path(matter_id, filename).write_text(json.dumps(payload, ensure_ascii=False))
