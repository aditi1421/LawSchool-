"""FastAPI entrypoint for the lawschool pipeline service."""

import logging
import os
import threading
from functools import lru_cache
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.artifacts.generate import AnthropicArtifactModel
from pipeline.export import artifacts_to_docx
from pipeline.ingest.extract import UnreadablePdf
from pipeline.db.repository import MatterRepository
from pipeline.embeddings import get_embedder
from pipeline.ingest.matter import DocumentRecord, MatterManifest
from pipeline.storage import get_storage
from pipeline.models import MatterArtifacts
from pipeline.query import AnthropicQueryModel, GroundedAnswer, answer_question
from pipeline.service import load_artifacts, retrieve, run_artifacts

logger = logging.getLogger(__name__)

app = FastAPI(title="lawschool pipeline")

# Dev CORS — the Next.js app on :3000 talks to this service on :8010.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

class CatchAllErrors(BaseHTTPMiddleware):
    """Turn unhandled exceptions into JSON 500s *inside* the CORS layer.

    Starlette's default 500 comes from ServerErrorMiddleware, which is the
    outermost layer — outside CORSMiddleware. Such a response carries no CORS
    headers, so a browser reports a server crash as an opaque network failure
    ("Failed to fetch") and the UI blames the connection instead of showing the
    real error. An `@app.exception_handler(Exception)` does not fix this: that
    handler is installed on ServerErrorMiddleware too. Catching here — with
    CORSMiddleware registered after (and therefore outside) this one — means the
    error response passes back out through CORS and reaches the client intact.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            logger.exception("unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": f"Server error: {type(exc).__name__}: {exc}"},
            )


# Registration order matters: add_middleware puts each new layer on the outside,
# so CORS must be added last to wrap CatchAllErrors.
app.add_middleware(CatchAllErrors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("LAWSCHOOL_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def get_store() -> MatterRepository:
    """The matter repository: Postgres + object storage.

    Cached — the embedder may load a local model, which must not happen per
    request.
    """
    return MatterRepository(storage=get_storage(), embedder=get_embedder())


_ocr_engine = None
_ocr_checked = False
# EasyOCR readers are not safe to share across concurrent calls, and OCR is
# CPU-bound anyway — serialize all OCR work behind this lock.
_ocr_lock = threading.Lock()


def get_ocr():
    """Singleton OCR engine — EasyOCR reader init is expensive, do it once."""
    global _ocr_engine, _ocr_checked
    if not _ocr_checked:
        from pipeline.ingest.extract import default_ocr_engine

        _ocr_engine = default_ocr_engine()
        _ocr_checked = True
    return _ocr_engine


def _ocr_worker(matter_id: str, filename: str) -> None:
    """Background OCR job.

    Runs in a worker thread (FastAPI runs sync background tasks in its
    threadpool), so the event loop stays free to serve other requests while
    pages are being read. Never raise into the task runner.
    """
    store = get_store()
    try:
        with _ocr_lock:
            engine = get_ocr()
            if engine is None:
                store.set_ocr_status(
                    matter_id, filename, "failed", "no OCR engine installed"
                )
                return
            store.ocr_document(matter_id, filename, engine)
    except Exception as exc:  # already recorded on the document; don't crash the worker
        logger.exception("OCR failed for %s/%s: %s", matter_id, filename, exc)


class CreateMatterRequest(BaseModel):
    title: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/matters")
def create_matter(req: CreateMatterRequest) -> MatterManifest:
    return get_store().create(title=req.title, today=date.today())


@app.get("/matters")
def list_matters() -> list[MatterManifest]:
    return get_store().list_matters()


@app.get("/matters/{matter_id}")
def get_matter(matter_id: str) -> MatterManifest:
    try:
        return get_store().get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")


@app.post("/matters/{matter_id}/files")
async def upload_file(
    matter_id: str, file: UploadFile, background: BackgroundTasks
) -> DocumentRecord:
    """Store a PDF and return immediately.

    Only the text layer is read here. Scanned pages are flagged and OCR'd by a
    background worker — OCR is CPU-bound and would otherwise block the event
    loop, freezing the whole API for the duration of the upload.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="only PDF uploads supported for now")
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    content = await file.read()
    try:
        record = await run_in_threadpool(store.add_pdf, matter_id, file.filename, content)
    except UnreadablePdf as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if record.ocr_status == "pending":
        background.add_task(_ocr_worker, matter_id, file.filename)
    return record


@app.delete("/matters/{matter_id}/files/{filename}", status_code=204)
def delete_file(matter_id: str, filename: str) -> None:
    """Remove one document from a matter (wrong file, superseded copy)."""
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    try:
        store.remove_document(matter_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found in this matter")


@app.post("/matters/{matter_id}/files/{filename}/ocr", status_code=202)
def start_ocr(matter_id: str, filename: str, background: BackgroundTasks) -> dict:
    """Queue (or retry) OCR for a stored document. Returns immediately."""
    store = get_store()
    try:
        manifest = store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    doc = next((d for d in manifest.documents if d.file == filename), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="file not found")
    if doc.ocr_status == "running":
        return {"ocr_status": "running"}
    store.set_ocr_status(matter_id, filename, "pending")
    background.add_task(_ocr_worker, matter_id, filename)
    return {"ocr_status": "pending"}


@app.delete("/matters/{matter_id}", status_code=204)
def delete_matter(matter_id: str) -> None:
    """Hard delete — DPDP 'delete my matter' removes files, pages, manifest."""
    try:
        get_store().delete(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")


@app.get("/matters/{matter_id}/files/{filename}")
def get_file(matter_id: str, filename: str) -> Response:
    """Serve a source PDF for the split-view verifier.

    Streamed from object storage. In production this should hand back a
    short-lived signed URL instead, so bytes go client->storage directly and
    the API is not in the data path.
    """
    try:
        content = get_store().file_bytes(matter_id, filename)
    except Exception:
        raise HTTPException(status_code=404, detail="file not found")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


class ChunkTextResponse(BaseModel):
    text: str


@app.get("/matters/{matter_id}/chunk")
def get_chunk_text(
    matter_id: str, file: str, page: int, para: int | None = None
) -> ChunkTextResponse:
    """Exact text of a cited paragraph — powers in-PDF highlighting."""
    store = get_store()
    try:
        manifest = store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    if not any(d.file == file for d in manifest.documents):
        raise HTTPException(status_code=404, detail="file not found")

    on_page = [
        c
        for c in store.matter_chunks(matter_id)
        if c.location.file == file and c.location.page == page
    ]
    if para is not None:
        exact = [c for c in on_page if c.location.para == para]
        if exact:
            return ChunkTextResponse(text=exact[0].text)
    if not on_page:
        raise HTTPException(status_code=404, detail="no text at cited location")
    return ChunkTextResponse(text="\n".join(c.text for c in on_page))


class ArtifactsResponse(BaseModel):
    artifacts: MatterArtifacts
    violations: list[dict]  # grounding violations removed before display


@app.post("/matters/{matter_id}/artifacts")
def generate_matter_artifacts(matter_id: str) -> ArtifactsResponse:
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    try:
        artifacts, violations = run_artifacts(store, matter_id, AnthropicArtifactModel())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ArtifactsResponse(
        artifacts=artifacts,
        violations=[
            {"kind": v.kind, "artifact": v.artifact, "claim": v.claim, "cite": v.cite.model_dump()}
            for v in violations
        ],
    )


@app.get("/matters/{matter_id}/artifacts")
def get_matter_artifacts(matter_id: str) -> MatterArtifacts:
    artifacts = load_artifacts(get_store(), matter_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="artifacts not generated yet")
    return artifacts


class QueryRequest(BaseModel):
    question: str


@app.post("/matters/{matter_id}/query")
def query_matter(matter_id: str, req: QueryRequest) -> GroundedAnswer:
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    retrieved = retrieve(store, matter_id, req.question)
    return answer_question(req.question, retrieved, AnthropicQueryModel())


class GenerateDraftRequest(BaseModel):
    doc_type: str  # DraftType value
    instructions: str = ""


class DraftResponse(BaseModel):
    draft_id: str
    draft: dict
    violations: list[dict]


@app.post("/matters/{matter_id}/drafts")
def create_draft(matter_id: str, req: GenerateDraftRequest) -> DraftResponse:
    from pipeline.drafting import AnthropicDraftModel, DraftType, generate_draft

    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    try:
        doc_type = DraftType(req.doc_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown doc_type: {req.doc_type}")
    chunks = store.matter_chunks(matter_id)
    if not chunks:
        raise HTTPException(status_code=422, detail="matter has no readable content")
    draft, violations = generate_draft(
        matter_id, doc_type, chunks, AnthropicDraftModel(), instructions=req.instructions
    )
    draft_id = store.save_draft(matter_id, doc_type.value, draft.model_dump(mode='json'))
    return DraftResponse(
        draft_id=draft_id,
        draft=draft.model_dump(mode="json"),
        violations=[
            {
                "kind": v.kind,
                "paragraph": v.paragraph,
                "cite": v.cite.model_dump() if v.cite else None,
            }
            for v in violations
        ],
    )


@app.get("/matters/{matter_id}/drafts")
def get_drafts(matter_id: str) -> list[dict]:
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    return store.list_drafts(matter_id)


# NOTE: the .docx route must be registered before the plain {draft_id} route —
# otherwise "{draft_id}" greedily matches "abc123.docx" and returns a JSON 404.
@app.get("/matters/{matter_id}/drafts/{draft_id}.docx")
def export_draft(matter_id: str, draft_id: str) -> Response:
    from pipeline.drafting.export import draft_to_docx
    from pipeline.drafting.models import DraftDocument

    try:
        draft = DraftDocument.model_validate(get_store().load_draft(matter_id, draft_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="draft not found")
    return Response(
        content=draft_to_docx(draft),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{draft.doc_type.value}-{draft_id}.docx"'
        },
    )


@app.get("/matters/{matter_id}/drafts/{draft_id}")
def get_draft(matter_id: str, draft_id: str) -> dict:
    try:
        return get_store().load_draft(matter_id, draft_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="draft not found")


@app.get("/matters/{matter_id}/export.docx")
def export_matter(matter_id: str) -> Response:
    artifacts = load_artifacts(get_store(), matter_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="artifacts not generated yet")
    return Response(
        content=artifacts_to_docx(artifacts),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{matter_id}-brief.docx"'},
    )
