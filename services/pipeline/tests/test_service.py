"""Service-level tests: record -> chunks -> artifacts -> query -> export.

Uses a fake model (no API calls) over real Postgres.
"""

from datetime import date
from pathlib import Path

from pipeline.export import artifacts_to_docx
from pipeline.db.repository import MatterRepository
from pipeline.models import ChronologyEvent, Citation, MatterArtifacts
from pipeline.query import GroundedAnswer, answer_question
from pipeline.service import load_artifacts, matter_chunks, retrieve, run_artifacts

from tests.conftest import requires_db
from tests.test_ingest import PLAINT_TEXT, ORDER_TEXT, make_text_pdf

pytestmark = requires_db


def seeded_store(repo: MatterRepository, tmp_path: Path) -> tuple[MatterRepository, str]:
    store = repo
    manifest = store.create("Sharma v. Verma", today=date(2026, 7, 15))
    for name, text in [("plaint.pdf", PLAINT_TEXT), ("order.pdf", ORDER_TEXT)]:
        pdf = tmp_path / name
        make_text_pdf(pdf, [text])
        store.add_pdf(manifest.matter_id, name, pdf.read_bytes())
    return store, manifest.matter_id


class FakeArtifactModel:
    def generate(self, system: str, user: str) -> MatterArtifacts:
        return MatterArtifacts(
            matter_id="x",
            chronology=[
                ChronologyEvent(
                    event_date=date(2019, 3, 12),
                    event="Sale deed executed between plaintiff and defendant no. 1",
                    cites=[Citation(file="plaint.pdf", page=1)],
                )
            ],
        )


class FakeQueryModel:
    def __init__(self, answer: GroundedAnswer) -> None:
        self._answer = answer

    def answer(self, system: str, user: str) -> GroundedAnswer:
        return self._answer


def test_matter_chunks_carry_provenance(repo: MatterRepository, tmp_path: Path) -> None:
    store, mid = seeded_store(repo, tmp_path)
    chunks = matter_chunks(store, mid)
    assert chunks
    assert {c.location.file for c in chunks} == {"plaint.pdf", "order.pdf"}


def test_run_artifacts_persists_and_reloads(repo: MatterRepository, tmp_path: Path) -> None:
    store, mid = seeded_store(repo, tmp_path)
    artifacts, violations = run_artifacts(store, mid, FakeArtifactModel())
    assert violations == []
    assert artifacts.matter_id == mid
    reloaded = load_artifacts(store, mid)
    assert reloaded is not None
    assert reloaded.chronology[0].event.startswith("Sale deed")


def test_retrieve_finds_relevant_page(repo: MatterRepository, tmp_path: Path) -> None:
    store, mid = seeded_store(repo, tmp_path)
    top = retrieve(store, mid, "when was the sale deed executed")
    assert top and top[0].location.file == "plaint.pdf"


def test_query_with_invalid_cites_refuses(repo: MatterRepository, tmp_path: Path) -> None:
    store, mid = seeded_store(repo, tmp_path)
    retrieved = retrieve(store, mid, "sale deed")
    hallucinated = GroundedAnswer(
        answer="The deed was registered at Tis Hazari",
        cites=[Citation(file="nonexistent.pdf", page=9)],
    )
    result = answer_question("where was the deed registered?", retrieved, FakeQueryModel(hallucinated))
    assert result.not_found
    assert result.answer == "not found in the record"


def test_export_docx(repo: MatterRepository, tmp_path: Path) -> None:
    store, mid = seeded_store(repo, tmp_path)
    artifacts, _ = run_artifacts(store, mid, FakeArtifactModel())
    blob = artifacts_to_docx(artifacts)
    assert blob[:2] == b"PK"  # valid zip container (docx)
    assert len(blob) > 1000
