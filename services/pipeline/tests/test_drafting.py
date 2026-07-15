"""Drafting workflow tests with a fake LLM — cite verification, placeholders, export."""

from datetime import date
from pathlib import Path

from pipeline.drafting import DraftDocument, DraftParagraph, DraftType, generate_draft, validate_draft
from pipeline.drafting.export import draft_to_docx
from pipeline.db.repository import MatterRepository
from pipeline.models import Citation

from tests.conftest import requires_db
from tests.test_artifacts import CHUNKS  # plaint.pdf p.3/p.4 + low-OCR scan.pdf p.1


def para(text: str, cites: list[Citation] | None = None, kind: str = "factual") -> DraftParagraph:
    return DraftParagraph(text=text, cites=cites or [], kind=kind)  # type: ignore[arg-type]


class FakeDraftModel:
    def __init__(self, draft: DraftDocument) -> None:
        self._draft = draft
        self.last_user: str | None = None

    def draft(self, system: str, user: str) -> DraftDocument:
        self.last_user = user
        return self._draft


def base_draft(paragraphs: list[DraftParagraph]) -> DraftDocument:
    return DraftDocument(
        matter_id="m1",
        doc_type=DraftType.LEGAL_NOTICE,
        title="LEGAL NOTICE",
        paragraphs=paragraphs,
        prayer=["Comply within 15 days."],
        missing_info=["[● full postal address of the noticee]"],
    )


def test_resolvable_cites_verify_and_unresolvable_are_stripped() -> None:
    draft = base_draft(
        [
            para("A sale deed was executed on 12.03.2019.", [Citation(file="plaint.pdf", page=3)]),
            para("You admitted liability in writing.", [Citation(file="reply.pdf", page=2)]),
        ]
    )
    clean, violations = validate_draft(draft, CHUNKS)

    assert clean.paragraphs[0].verified is True
    assert clean.paragraphs[1].verified is False
    assert clean.paragraphs[1].cites == []  # fabricated cite stripped, text kept
    kinds = {v.kind for v in violations}
    assert "unresolvable_citation" in kinds
    assert "uncited_factual_paragraph" in kinds  # after stripping it has no source


def test_placeholder_paragraph_is_acceptable_without_cites() -> None:
    draft = base_draft(
        [para("Your registered office at [● address of noticee] was served earlier.")]
    )
    clean, violations = validate_draft(draft, CHUNKS)
    assert violations == []
    assert clean.paragraphs[0].verified is True


def test_boilerplate_needs_no_cites() -> None:
    draft = base_draft(
        [para("Verified at Delhi on this day that the contents are true.", kind="boilerplate")]
    )
    clean, violations = validate_draft(draft, CHUNKS)
    assert violations == []
    assert clean.paragraphs[0].verified is True


def test_model_set_verified_flag_is_discarded() -> None:
    lying = DraftParagraph(
        text="An invented fact with no source.", kind="factual", cites=[], verified=True
    )
    clean, violations = validate_draft(base_draft([lying]), CHUNKS)
    assert clean.paragraphs[0].verified is False  # recomputed in code
    assert violations


def test_generate_draft_stamps_ids_and_passes_instructions() -> None:
    model = FakeDraftModel(base_draft([para("x [● y]")]))
    draft, _ = generate_draft(
        "m1", DraftType.LEGAL_NOTICE, CHUNKS, model, instructions="demand interest at 12%"
    )
    assert draft.matter_id == "m1"
    assert draft.doc_type == DraftType.LEGAL_NOTICE
    assert model.last_user is not None
    assert "demand interest at 12%" in model.last_user
    assert "[plaint.pdf | page 3 | para 1]" in model.last_user


def test_draft_docx_export_and_missing_info_notes() -> None:
    draft = base_draft(
        [para("A sale deed was executed on 12.03.2019.", [Citation(file="plaint.pdf", page=3)])]
    )
    clean, _ = validate_draft(draft, CHUNKS)
    blob = draft_to_docx(clean)
    assert blob[:2] == b"PK"
    assert len(blob) > 1000


@requires_db
def test_draft_store_roundtrip(repo: MatterRepository) -> None:
    m = repo.create("Test", today=date(2026, 7, 15))
    draft = base_draft([para("x [● y]")]).model_copy(update={"matter_id": m.matter_id})

    draft_id = repo.save_draft(m.matter_id, draft.doc_type.value, draft.model_dump(mode="json"))
    loaded = DraftDocument.model_validate(repo.load_draft(m.matter_id, draft_id))
    assert loaded.title == "LEGAL NOTICE"

    listing = repo.list_drafts(m.matter_id)
    assert listing[0]["draft_id"] == draft_id
    assert listing[0]["doc_type"] == "legal_notice"
    assert listing[0]["missing_info"] == 1
