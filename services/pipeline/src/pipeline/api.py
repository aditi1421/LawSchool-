"""FastAPI entrypoint for the lawschool pipeline service."""

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pipeline.artifacts.generate import AnthropicArtifactModel
from pipeline.export import artifacts_to_docx
from pipeline.ingest.matter import DocumentRecord, MatterManifest, MatterStore
from pipeline.models import MatterArtifacts
from pipeline.query import AnthropicQueryModel, GroundedAnswer, answer_question
from pipeline.service import load_artifacts, retrieve, run_artifacts

app = FastAPI(title="lawschool pipeline")

# Dev CORS — the Next.js app on :3000 talks to this service on :8000.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("LAWSCHOOL_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_store() -> MatterStore:
    root = Path(os.environ.get("LAWSCHOOL_DATA_DIR", "data/matters"))
    return MatterStore(root)


_ocr_engine = None
_ocr_checked = False


def get_ocr():
    """Singleton OCR engine — EasyOCR reader init is expensive, do it once."""
    global _ocr_engine, _ocr_checked
    if not _ocr_checked:
        from pipeline.ingest.extract import default_ocr_engine

        _ocr_engine = default_ocr_engine()
        _ocr_checked = True
    return _ocr_engine


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
async def upload_file(matter_id: str, file: UploadFile) -> DocumentRecord:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="only PDF uploads supported for now")
    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    content = await file.read()
    return store.add_pdf(matter_id, file.filename, content, ocr=get_ocr())


@app.delete("/matters/{matter_id}", status_code=204)
def delete_matter(matter_id: str) -> None:
    """Hard delete — DPDP 'delete my matter' removes files, pages, manifest."""
    try:
        get_store().delete(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")


@app.get("/matters/{matter_id}/files/{filename}")
def get_file(matter_id: str, filename: str) -> FileResponse:
    """Serve a source PDF for the split-view verifier."""
    store = get_store()
    path = (store.files_dir(matter_id) / filename).resolve()
    if not path.is_relative_to(store.files_dir(matter_id).resolve()) or not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="application/pdf")


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
    doc = next((d for d in manifest.documents if d.file == file), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="file not found")

    from pipeline.structure import chunk_pages

    chunks = chunk_pages(matter_id, file, doc.doc_type, store.load_pages(matter_id, file))
    on_page = [c for c in chunks if c.location.page == page]
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
    from pipeline.drafting.store import save_draft
    from pipeline.service import matter_chunks as _chunks

    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    try:
        doc_type = DraftType(req.doc_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown doc_type: {req.doc_type}")
    chunks = _chunks(store, matter_id)
    if not chunks:
        raise HTTPException(status_code=422, detail="matter has no readable content")
    draft, violations = generate_draft(
        matter_id, doc_type, chunks, AnthropicDraftModel(), instructions=req.instructions
    )
    draft_id = save_draft(store, draft)
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
    from pipeline.drafting.store import list_drafts

    store = get_store()
    try:
        store.get(matter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="matter not found")
    return list_drafts(store, matter_id)


# NOTE: the .docx route must be registered before the plain {draft_id} route —
# otherwise "{draft_id}" greedily matches "abc123.docx" and returns a JSON 404.
@app.get("/matters/{matter_id}/drafts/{draft_id}.docx")
def export_draft(matter_id: str, draft_id: str) -> Response:
    from pipeline.drafting.export import draft_to_docx
    from pipeline.drafting.store import load_draft

    try:
        draft = load_draft(get_store(), matter_id, draft_id)
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
    from pipeline.drafting.store import load_draft

    try:
        return load_draft(get_store(), matter_id, draft_id).model_dump(mode="json")
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
