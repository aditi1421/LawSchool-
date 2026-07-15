"""Eval harness tests over a small synthetic matter.

The fixture simulates a two-document civil record; tests drive the three
metrics through correct, wrongly-cited, fabricated, and incomplete outputs.
"""

from datetime import date

from pipeline.evals import GATE, GoldEvent, GoldMatter, LexicalJudge, run_gate, score_matter
from pipeline.models import Citation, ChronologyEvent, MatterArtifacts

PAGES = {
    ("plaint.pdf", 3): (
        "The plaintiff states that a sale deed for the suit property was "
        "executed on 12 March 2019 between the plaintiff and defendant no. 1."
    ),
    ("plaint.pdf", 4): (
        "Defendant no. 1 failed to hand over vacant possession of the suit "
        "property despite repeated demands by the plaintiff."
    ),
    ("order.pdf", 1): (
        "This court directs the parties to maintain status quo over the suit "
        "property until the next date of hearing."
    ),
}

GOLD = GoldMatter(
    matter_id="synthetic-civil-1",
    files=["plaint.pdf", "order.pdf"],
    events=[
        GoldEvent(
            event_date=date(2019, 3, 12),
            description="Sale deed executed between plaintiff and defendant no. 1",
            source=Citation(file="plaint.pdf", page=3),
        ),
    ],
)

JUDGE = LexicalJudge()


def make_event(text: str, file: str, page: int, when: date | None = None) -> ChronologyEvent:
    return ChronologyEvent(
        event_date=when, event=text, cites=[Citation(file=file, page=page)]
    )


def artifacts_with(events: list[ChronologyEvent]) -> MatterArtifacts:
    return MatterArtifacts(matter_id="synthetic-civil-1", chronology=events)


def test_correct_output_passes_gate() -> None:
    good = artifacts_with(
        [
            make_event(
                "Sale deed executed between plaintiff and defendant no. 1",
                "plaint.pdf",
                3,
                date(2019, 3, 12),
            )
        ]
    )
    report = score_matter(GOLD, good, PAGES, JUDGE)
    assert report.citation_accuracy == 1.0
    assert report.fabrication_count == 0
    assert report.chronology_recall == 1.0
    assert run_gate([report]).passed


def test_wrong_page_cite_is_citation_error_not_fabrication() -> None:
    wrong_cite = artifacts_with(
        [
            make_event(
                "Sale deed executed between plaintiff and defendant no. 1",
                "order.pdf",  # claim is true, but cited to the wrong document
                1,
                date(2019, 3, 12),
            )
        ]
    )
    report = score_matter(GOLD, wrong_cite, PAGES, JUDGE)
    assert report.citation_accuracy == 0.0
    assert report.fabrication_count == 0  # supported elsewhere in the record
    assert not run_gate([report]).passed


def test_fabricated_claim_fails_gate() -> None:
    fabricated = artifacts_with(
        [
            make_event(
                "Sale deed executed between plaintiff and defendant no. 1",
                "plaint.pdf",
                3,
                date(2019, 3, 12),
            ),
            make_event(
                "Defendant no. 1 admitted liability in a registered settlement agreement",
                "plaint.pdf",
                4,
            ),
        ]
    )
    report = score_matter(GOLD, fabricated, PAGES, JUDGE)
    assert report.fabrication_count == 1
    result = run_gate([report])
    assert not result.passed
    assert any("fabrication" in f for f in result.failures)


def test_missed_gold_event_lowers_recall() -> None:
    incomplete = artifacts_with(
        [
            make_event(
                "Court directed parties to maintain status quo over the suit property",
                "order.pdf",
                1,
            )
        ]
    )
    report = score_matter(GOLD, incomplete, PAGES, JUDGE)
    assert report.chronology_recall == 0.0
    assert report.missed_gold_events == GOLD.events
    assert not run_gate([report]).passed


def test_empty_gold_set_cannot_pass_gate() -> None:
    result = run_gate([])
    assert not result.passed


def test_gate_thresholds() -> None:
    assert GATE.min_citation_accuracy == 0.98
    assert GATE.max_fabrications == 0
    assert GATE.min_chronology_recall == 0.90
