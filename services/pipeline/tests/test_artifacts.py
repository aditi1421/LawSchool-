"""Artifact agent tests with a fake LLM — verifies the code-enforced honesty rules."""

from datetime import date

from pipeline.artifacts import generate_artifacts, validate_grounding
from pipeline.artifacts.generate import build_context
from pipeline.models import (
    Chunk,
    ChronologyEvent,
    Citation,
    DocType,
    Language,
    MatterArtifacts,
    SidePosition,
    Contention,
)


def chunk(file: str, page: int, text: str, para: int | None = 1, ocr: float = 1.0) -> Chunk:
    return Chunk(
        matter_id="m1",
        location=Citation(file=file, page=page, para=para),
        text=text,
        doc_type=DocType.PLAINT,
        language=Language.ENGLISH,
        ocr_confidence=ocr,
    )


CHUNKS = [
    chunk("plaint.pdf", 3, "A sale deed was executed on 12.03.2019."),
    chunk("plaint.pdf", 4, "Possession was never handed over."),
    chunk("scan.pdf", 1, "faded text about a notice", ocr=0.4),
]


def event(text: str, cites: list[Citation], when: date | None = None) -> ChronologyEvent:
    return ChronologyEvent(event_date=when, event=text, cites=cites)


class FakeModel:
    def __init__(self, artifacts: MatterArtifacts) -> None:
        self.artifacts = artifacts
        self.last_user: str | None = None

    def generate(self, system: str, user: str) -> MatterArtifacts:
        self.last_user = user
        return self.artifacts


def test_context_carries_provenance_tags_and_low_ocr_flags() -> None:
    ctx = build_context(CHUNKS)
    assert "[plaint.pdf | page 3 | para 1]" in ctx
    assert "LOW-CONFIDENCE OCR" in ctx  # the scan page is flagged for the model


def test_unresolvable_citation_is_removed_and_reported() -> None:
    fabricated = MatterArtifacts(
        matter_id="m1",
        chronology=[
            event("Sale deed executed", [Citation(file="plaint.pdf", page=3)], date(2019, 3, 12)),
            event("Settlement agreement signed", [Citation(file="plaint.pdf", page=99)]),  # no such page
        ],
    )
    clean, violations = validate_grounding(fabricated, CHUNKS)
    assert len(clean.chronology) == 1  # fabricated-cite claim removed
    assert clean.chronology[0].event == "Sale deed executed"
    assert len(violations) == 1
    assert violations[0].kind == "unresolvable_citation"
    assert violations[0].cite.page == 99


def test_low_ocr_only_support_is_downgraded_not_trusted() -> None:
    arts = MatterArtifacts(
        matter_id="m1",
        chronology=[event("A notice was sent", [Citation(file="scan.pdf", page=1)])],
    )
    clean, violations = validate_grounding(arts, CHUNKS)
    assert violations == []
    assert clean.chronology[0].confidence == "low_ocr"


def test_one_sided_contention_survives_when_other_side_unresolvable() -> None:
    arts = MatterArtifacts(
        matter_id="m1",
        contentions=[
            Contention(
                issue="possession",
                petitioner=SidePosition(
                    position="Possession was never handed over",
                    cites=[Citation(file="plaint.pdf", page=4)],
                ),
                respondent=SidePosition(
                    position="Possession was delivered",
                    cites=[Citation(file="ws.pdf", page=2)],  # document not in record
                ),
            )
        ],
    )
    clean, violations = validate_grounding(arts, CHUNKS)
    assert len(clean.contentions) == 1
    assert clean.contentions[0].petitioner is not None
    assert clean.contentions[0].respondent is None  # unresolvable side dropped
    assert len(violations) == 1


def test_generate_artifacts_stamps_matter_and_feeds_tagged_context() -> None:
    model = FakeModel(
        MatterArtifacts(
            matter_id="wrong",  # model output gets overridden by pipeline values
            chronology=[
                event("Sale deed executed", [Citation(file="plaint.pdf", page=3)], date(2019, 3, 12))
            ],
        )
    )
    clean, violations = generate_artifacts("m1", CHUNKS, model)
    assert clean.matter_id == "m1"
    assert violations == []
    assert model.last_user and "[plaint.pdf | page 3 | para 1]" in model.last_user
