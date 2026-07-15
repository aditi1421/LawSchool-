"""Fidelity tests — do asserted dates and amounts occur in the cited page?

The headline case is real: llama3.2 cited the correct page and paragraph and
recorded 12.03.2019 as the year 1203. Grounding passed it because the citation
resolved. See pipeline/artifacts/fidelity.py.
"""

from datetime import date

from pipeline.artifacts import validate_fidelity, validate_grounding
from pipeline.artifacts.generate import generate_artifacts
from pipeline.models import (
    Chunk,
    ChronologyEvent,
    Citation,
    Contention,
    DocType,
    Language,
    MatterArtifacts,
    OrderEntry,
    SidePosition,
)
from pipeline.structure.amounts import amounts_in, extract_amounts

PLAINT_P1 = (
    "A registered sale deed dated 12.03.2019 was executed between the plaintiff "
    "and defendant no. 1, Shri Mohan Verma, for a consideration of Rs. 85,00,000."
)
ORDER_P1 = (
    "It is ordered that the parties shall maintain status quo. Costs of Rs. 5,000 "
    "are imposed. List on 14.08.2023."
)

CHUNKS = [
    Chunk(
        matter_id="m1",
        location=Citation(file="plaint.pdf", page=1, para=2),
        text=PLAINT_P1,
        doc_type=DocType.PLAINT,
        language=Language.ENGLISH,
        ocr_confidence=1.0,
    ),
    Chunk(
        matter_id="m1",
        location=Citation(file="order.pdf", page=1, para=None),
        text=ORDER_P1,
        doc_type=DocType.ORDER,
        language=Language.ENGLISH,
        ocr_confidence=1.0,
    ),
]

PLAINT_CITE = [Citation(file="plaint.pdf", page=1, para=2)]
ORDER_CITE = [Citation(file="order.pdf", page=1, para=None)]


def arts(**kw) -> MatterArtifacts:
    return MatterArtifacts(matter_id="m1", **kw)


# -- the regression this module exists for -----------------------------------


def test_the_1203_bug():
    """llama3.2's actual output: right page, right paragraph, year 1203."""
    bad = arts(
        chronology=[
            ChronologyEvent(
                event_date=date(1203, 1, 19),  # the record says 2019-03-12
                event="Registered sale deed executed",
                cites=PLAINT_CITE,
            )
        ]
    )
    # Grounding passes it — the citation resolves. That is the whole problem.
    grounded, grounding_violations = validate_grounding(bad, CHUNKS)
    assert grounding_violations == []
    assert grounded.chronology[0].event_date == date(1203, 1, 19)

    # Fidelity catches it.
    clean, violations = validate_fidelity(grounded, CHUNKS)
    assert len(violations) == 1
    assert violations[0].kind == "unsupported_date"
    assert violations[0].asserted == "1203-01-19"

    # The event survives; only the invented date is stripped. It is probably a
    # real event with a hallucinated date, and "undated" is the honest place.
    assert len(clean.chronology) == 1
    assert clean.chronology[0].event == "Registered sale deed executed"
    assert clean.chronology[0].event_date is None


def test_a_date_the_record_states_is_kept() -> None:
    good = arts(
        chronology=[
            ChronologyEvent(
                event_date=date(2019, 3, 12), event="Sale deed executed", cites=PLAINT_CITE
            )
        ]
    )
    clean, violations = validate_fidelity(good, CHUNKS)
    assert violations == []
    assert clean.chronology[0].event_date == date(2019, 3, 12)


def test_an_undated_event_is_untouched() -> None:
    ev = arts(chronology=[ChronologyEvent(event="Possession withheld", cites=PLAINT_CITE)])
    clean, violations = validate_fidelity(ev, CHUNKS)
    assert violations == []
    assert clean.chronology[0].event_date is None


def test_a_date_from_another_document_does_not_count() -> None:
    """14.08.2023 is in the record — but on order.pdf, not the page cited."""
    wrong = arts(
        chronology=[
            ChronologyEvent(
                event_date=date(2023, 8, 14), event="Listed for hearing", cites=PLAINT_CITE
            )
        ]
    )
    clean, violations = validate_fidelity(wrong, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_date"]
    assert clean.chronology[0].event_date is None


# -- amounts ------------------------------------------------------------------


def test_an_invented_amount_removes_the_claim() -> None:
    """Rs. 8,50,000 reads plausibly and is a different case from Rs. 85,00,000.
    The figure is inside the prose, so there is nothing to null."""
    bad = arts(
        chronology=[
            ChronologyEvent(
                event_date=date(2019, 3, 12),
                event="Sale deed executed for Rs. 8,50,000",
                cites=PLAINT_CITE,
            )
        ]
    )
    clean, violations = validate_fidelity(bad, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_amount"]
    assert "8,50,000" in violations[0].asserted
    assert clean.chronology == []


def test_an_amount_the_record_states_is_kept() -> None:
    good = arts(
        chronology=[
            ChronologyEvent(
                event_date=date(2019, 3, 12),
                event="Sale deed executed for Rs. 85,00,000",
                cites=PLAINT_CITE,
            )
        ]
    )
    clean, violations = validate_fidelity(good, CHUNKS)
    assert violations == []
    assert len(clean.chronology) == 1


def test_formatting_of_an_amount_does_not_matter() -> None:
    """'Rs. 85,00,000/-' and '₹8500000' assert the same number."""
    for text in ("paid Rs. 85,00,000/-", "paid ₹8500000", "paid Rs 85,00,000"):
        a = arts(chronology=[ChronologyEvent(event=text, cites=PLAINT_CITE)])
        _, violations = validate_fidelity(a, CHUNKS)
        assert violations == [], f"{text!r} should match the record"


# -- other artifact kinds ------------------------------------------------------


def test_order_dates_are_checked_field_by_field() -> None:
    order = arts(
        proceedings=[
            OrderEntry(
                order_date=date(2023, 8, 14),  # on the page
                next_date=date(2099, 1, 1),  # invented
                direction="Parties to maintain status quo",
                cites=ORDER_CITE,
            )
        ]
    )
    clean, violations = validate_fidelity(order, CHUNKS)
    assert [v.artifact for v in violations] == ["proceedings.next_date"]
    assert clean.proceedings[0].order_date == date(2023, 8, 14)  # kept
    assert clean.proceedings[0].next_date is None  # stripped


def test_a_contention_with_an_invented_amount_loses_that_side() -> None:
    cont = arts(
        contentions=[
            Contention(
                issue="consideration",
                petitioner=SidePosition(
                    position="Paid Rs. 85,00,000 in full", cites=PLAINT_CITE
                ),
                respondent=SidePosition(
                    position="Only Rs. 1,00,000 was ever paid", cites=PLAINT_CITE
                ),
            )
        ]
    )
    clean, violations = validate_fidelity(cont, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_amount"]
    assert clean.contentions[0].petitioner is not None
    assert clean.contentions[0].respondent is None


def test_a_contention_with_no_side_left_is_dropped() -> None:
    cont = arts(
        contentions=[
            Contention(
                issue="consideration",
                petitioner=SidePosition(position="Paid Rs. 999 only", cites=PLAINT_CITE),
            )
        ]
    )
    clean, violations = validate_fidelity(cont, CHUNKS)
    assert violations and clean.contentions == []  # asserts nothing


# -- the pipeline runs both passes ---------------------------------------------


def test_generate_artifacts_applies_fidelity_for_any_provider() -> None:
    """The check is pure code, so it protects a local model exactly as much as
    Claude — which matters, since the weaker model needs it more."""

    class FakeModel:
        def generate(self, system: str, user: str) -> MatterArtifacts:
            return arts(
                chronology=[
                    ChronologyEvent(
                        event_date=date(1203, 1, 19),
                        event="Registered sale deed executed",
                        cites=PLAINT_CITE,
                    )
                ]
            )

    clean, violations = generate_artifacts("m1", CHUNKS, FakeModel())
    assert [v.kind for v in violations] == ["unsupported_date"]
    assert clean.chronology[0].event_date is None


# -- the amount extractor ------------------------------------------------------


def test_indian_grouping_is_read_as_lakhs_not_millions() -> None:
    # 85,00,000 is eighty-five lakh. A thousands-grouping parser reads 8.5 crore.
    assert amounts_in("Rs. 85,00,000") == {8_500_000}
    assert amounts_in("Rs. 5,000") == {5_000}
    assert amounts_in("₹1,00,000/-") == {100_000}


def test_scale_words_multiply() -> None:
    assert amounts_in("Rs. 8.5 lakhs") == {850_000}
    assert amounts_in("Rs. 2 crore") == {20_000_000}


def test_extractor_ignores_bare_numbers() -> None:
    # Section numbers, page numbers and years are not money.
    assert amounts_in("under section 138 in the year 2019 at page 42") == set()
    assert extract_amounts("no figures here") == []


def test_ungrouped_figures_are_read_whole() -> None:
    """Regression: a grouping-validating regex read '₹8500000' as 850, because
    alternation takes the first branch that matches, not the longest."""
    assert amounts_in("₹8500000") == {8_500_000}
    assert amounts_in("Rs 850") == {850}
    assert amounts_in("Rs. 1234567") == {1_234_567}


def test_amounts_are_shown_back_in_indian_grouping() -> None:
    from pipeline.structure.amounts import format_indian

    # A lawyer reads '85,00,000'. Python's f"{v:,}" gives '8,500,000' — the
    # wrong system, and a different number to the eye.
    assert format_indian(8_500_000) == "85,00,000"
    assert format_indian(100_000) == "1,00,000"
    assert format_indian(5_000) == "5,000"
    assert format_indian(850) == "850"
    assert format_indian(20_000_000) == "2,00,00,000"
