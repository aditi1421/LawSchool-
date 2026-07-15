"""The drafting verify-and-revise loop, with fake models and judges.

Covers the three layers on drafts (resolution / fidelity / risk-gated
support), the bounded loop with failure-specific revision prompts, the
derived List of Dates, and the SLP consuming Synopsis + List of Dates as
components. No network, no API keys — same policy as every other test here.
"""

from datetime import date

import pytest

from pipeline.drafting import (
    DraftDidNotConverge,
    DraftDocument,
    DraftParagraph,
    DraftType,
    ListOfDatesEntry,
    assertion_risks,
    derive_list_of_dates,
    generate_draft,
    validate_draft,
    verify_draft,
)
from pipeline.drafting.export import draft_to_docx
from pipeline.models import (
    ChronologyEvent,
    Chunk,
    Citation,
    DocType,
    Language,
    MatterArtifacts,
)

PLAINT_P1 = (
    "A registered sale deed dated 12.03.2019 was executed between the plaintiff "
    "and defendant no. 1 for a consideration of Rs. 85,00,000, and the defendant "
    "agreed to pay the balance within thirty days."
)
ORDER_P1 = (
    "It is ordered that the parties shall maintain status quo. Costs of Rs. 5,000 "
    "are imposed. List on 14.08.2023."
)
JUDGMENT_P2 = (
    "By judgment dated 05.01.2024 the appeal was dismissed and the decree of the "
    "trial court was affirmed."
)


def chunk(file: str, page: int, text: str, para: int | None = 1) -> Chunk:
    return Chunk(
        matter_id="m1",
        location=Citation(file=file, page=page, para=para),
        text=text,
        doc_type=DocType.PLAINT,
        language=Language.ENGLISH,
        ocr_confidence=1.0,
    )


CHUNKS = [
    chunk("plaint.pdf", 1, PLAINT_P1, para=2),
    chunk("order.pdf", 1, ORDER_P1, para=None),
    chunk("judgment.pdf", 2, JUDGMENT_P2),
]

PLAINT_CITE = [Citation(file="plaint.pdf", page=1, para=2)]
ORDER_CITE = [Citation(file="order.pdf", page=1)]
JUDGMENT_CITE = [Citation(file="judgment.pdf", page=2, para=1)]


def para(text: str, cites: list[Citation] | None = None, kind: str = "factual") -> DraftParagraph:
    return DraftParagraph(text=text, cites=cites or [], kind=kind)  # type: ignore[arg-type]


def doc(paragraphs: list[DraftParagraph], **kw) -> DraftDocument:
    return DraftDocument(
        matter_id="m1",
        doc_type=DraftType.LEGAL_NOTICE,
        title="LEGAL NOTICE",
        paragraphs=paragraphs,
        **kw,
    )


class SeqDraftModel:
    """Returns queued drafts in order; records every (system, user) call."""

    def __init__(self, drafts: list[DraftDocument]) -> None:
        self._queue = list(drafts)
        self.calls: list[tuple[str, str]] = []

    def draft(self, system: str, user: str) -> DraftDocument:
        self.calls.append((system, user))
        return self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]


class RecordingJudge:
    """SupportJudge fake: records what it was asked; verdict by claim lookup."""

    def __init__(self, reject_containing: str | None = None) -> None:
        self.asked: list[str] = []
        self._reject = reject_containing

    def supports(self, claim: str, page_text: str) -> bool:
        self.asked.append(claim)
        return self._reject is None or self._reject not in claim


# -- the risk test --------------------------------------------------------------


def test_assertion_risks_names_why_a_paragraph_is_risky() -> None:
    assert assertion_risks("The deed was executed on 12.03.2019") == {"date"}
    assert assertion_risks("A sum of Rs. 85,00,000 was paid") == {"amount"}
    assert assertion_risks("The defendant agreed to pay the balance") == {"obligation"}
    assert assertion_risks("On 12.03.2019 the defendant became liable for Rs. 5,000") == {
        "date",
        "amount",
        "obligation",
    }


def test_plain_narrative_is_not_risky() -> None:
    # No date, no amount, no obligation: not worth a judge call.
    assert assertion_risks("The plaintiff resides in Delhi and runs a shop") == frozenset()


def test_placeholders_are_gaps_not_assertions() -> None:
    # A figure inside [● ...] is the thing the draft is NOT claiming.
    assert assertion_risks("Notice was served on [● date, e.g. 12.03.2019]") == frozenset()


# -- fidelity on drafts ----------------------------------------------------------


def test_a_date_the_cited_page_states_passes() -> None:
    d = doc([para("A sale deed dated 12.03.2019 was executed.", PLAINT_CITE)])
    assert verify_draft(d, CHUNKS) == []


def test_a_date_the_cited_page_does_not_state_fails() -> None:
    d = doc([para("A sale deed dated 13.03.2019 was executed.", PLAINT_CITE)])
    violations = verify_draft(d, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_date"]
    assert violations[0].asserted == "13.03.2019"
    assert violations[0].where == "paragraphs[0]"


def test_a_date_from_the_wrong_page_fails() -> None:
    # 14.08.2023 is in the record — on order.pdf, not the page cited.
    d = doc([para("The matter was listed on 14.08.2023.", PLAINT_CITE)])
    assert [v.kind for v in verify_draft(d, CHUNKS)] == ["unsupported_date"]


def test_an_invented_amount_fails_with_indian_grouping() -> None:
    d = doc([para("The consideration was Rs. 8,50,000.", PLAINT_CITE)])
    violations = verify_draft(d, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_amount"]
    assert violations[0].asserted == "Rs. 8,50,000"


def test_boilerplate_dates_are_exempt() -> None:
    # A verification clause carries the signing date; it asserts nothing from
    # the record.
    d = doc([para("Verified at Delhi on 16.07.2026.", kind="boilerplate")])
    assert verify_draft(d, CHUNKS) == []


def test_a_placeholder_cannot_carry_a_date() -> None:
    """A factual paragraph may stand on a placeholder — but a specific date it
    asserts still has to trace. [●] marks a gap; it is not a source."""
    d = doc([para("On 13.03.2019 the noticee at [● address] defaulted.")])
    kinds = [v.kind for v in verify_draft(d, CHUNKS)]
    assert kinds == ["unsupported_date"]  # placeholder excuses the missing cite only


def test_prayer_amounts_must_occur_somewhere_in_the_record() -> None:
    ok = doc([], prayer=["Decree for Rs. 85,00,000 with interest."])
    assert verify_draft(ok, CHUNKS) == []
    bad = doc([], prayer=["Decree for Rs. 99,00,000 with interest."])
    violations = verify_draft(bad, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_amount"]
    assert violations[0].where == "prayer[0]"


def test_court_header_and_title_figures_are_checked_against_the_record() -> None:
    """The header cannot carry citations, but a figure the model writes there
    is still an assertion — checked record-wide. Found by review: an invented
    date in the cause title used to sail through every layer."""
    bad = doc([]).model_copy(
        update={
            "court_header": (
                "IN THE SUPREME COURT OF INDIA\nAgainst the judgment dated "
                "06.01.2024 of the High Court"  # the record says 05.01.2024
            )
        }
    )
    violations = verify_draft(bad, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_date"]
    assert violations[0].where == "court_header"
    assert violations[0].asserted == "06.01.2024"

    ok = bad.model_copy(
        update={
            "court_header": (
                "IN THE SUPREME COURT OF INDIA\nAgainst the judgment dated "
                "05.01.2024 of the High Court"
            )
        }
    )
    assert verify_draft(ok, CHUNKS) == []


def test_header_placeholders_are_not_assertions() -> None:
    d = doc([]).model_copy(update={"court_header": "SLP (CIVIL) NO. [● number] OF [● year]"})
    assert verify_draft(d, CHUNKS) == []


def test_prayer_dates_are_checked_record_wide_too() -> None:
    bad = doc([], prayer=["Stay the operation of the judgment dated 06.01.2024."])
    violations = verify_draft(bad, CHUNKS)
    assert [v.kind for v in violations] == ["unsupported_date"]
    assert violations[0].where == "prayer[0]"


def test_resolution_failures_are_reported_with_location() -> None:
    d = doc(
        [
            para("You admitted liability in writing.", [Citation(file="reply.pdf", page=2)]),
            para("An unsourced factual assertion."),
        ]
    )
    kinds = {v.kind for v in verify_draft(d, CHUNKS)}
    assert kinds == {"unresolvable_citation", "uncited_factual_paragraph"}


# -- risk-gated support judging ---------------------------------------------------


def test_only_risky_cited_paragraphs_spend_a_judge_call() -> None:
    judge = RecordingJudge()
    d = doc(
        [
            para("The defendant agreed to pay the balance.", PLAINT_CITE),  # risky
            para("The plaintiff runs a shop in the suit premises.", PLAINT_CITE),  # not risky
            para("Prayer language of a formal kind.", kind="boilerplate"),
        ]
    )
    assert verify_draft(d, CHUNKS, judge=judge) == []
    assert judge.asked == ["The defendant agreed to pay the balance."]


def test_judge_rejection_is_an_unsupported_claim() -> None:
    judge = RecordingJudge(reject_containing="undertook to vacate")
    d = doc([para("The defendant undertook to vacate the premises.", PLAINT_CITE)])
    violations = verify_draft(d, CHUNKS, judge=judge)
    assert [v.kind for v in violations] == ["unsupported_claim"]
    assert violations[0].asserted == "obligation"


def test_a_paragraph_that_failed_the_free_layers_is_not_judged() -> None:
    judge = RecordingJudge()
    d = doc([para("The defendant agreed to pay Rs. 99,00,000.", PLAINT_CITE)])
    violations = verify_draft(d, CHUNKS, judge=judge)
    assert [v.kind for v in violations] == ["unsupported_amount"]
    assert judge.asked == []  # the call would have been wasted


def test_without_a_judge_the_free_layers_still_run() -> None:
    d = doc([para("A sale deed dated 13.03.2019 was executed.", PLAINT_CITE)])
    assert [v.kind for v in verify_draft(d, CHUNKS, judge=None)] == ["unsupported_date"]


# -- the verify-and-revise loop ----------------------------------------------------


def test_the_loop_revises_what_failed_and_converges() -> None:
    bad = doc([para("A sale deed dated 13.03.2019 was executed.", PLAINT_CITE)])
    good = doc([para("A sale deed dated 12.03.2019 was executed.", PLAINT_CITE)])
    model = SeqDraftModel([bad, good])

    draft, run = generate_draft("m1", DraftType.LEGAL_NOTICE, CHUNKS, model)

    assert run.attempts == 2
    assert [len(r) for r in run.rounds] == [1, 0]
    assert draft.paragraphs[0].text == "A sale deed dated 12.03.2019 was executed."
    assert draft.paragraphs[0].verified is True


def test_the_revision_prompt_shows_the_specific_failure() -> None:
    bad = doc([para("A sale deed dated 13.03.2019 was executed.", PLAINT_CITE)])
    good = doc([para("A sale deed dated 12.03.2019 was executed.", PLAINT_CITE)])
    model = SeqDraftModel([bad, good])
    generate_draft("m1", DraftType.LEGAL_NOTICE, CHUNKS, model)

    revision = model.calls[1][1]
    assert "13.03.2019" in revision  # the figure it asserted
    assert "does not appear" in revision
    assert "12.03.2019" in revision  # what the cited page actually states
    assert "YOUR PREVIOUS DRAFT" in revision
    assert "Never invent" in revision


def test_a_loop_that_cannot_converge_fails_loudly() -> None:
    bad = doc([para("A sale deed dated 13.03.2019 was executed.", PLAINT_CITE)])
    model = SeqDraftModel([bad])  # returns the same bad draft forever

    with pytest.raises(DraftDidNotConverge) as exc:
        generate_draft("m1", DraftType.LEGAL_NOTICE, CHUNKS, model, max_revisions=2)

    assert "could not be verified" in str(exc.value)
    assert "unsupported_date" in str(exc.value)
    assert "Nothing was saved" in str(exc.value)
    assert model.calls and len(model.calls) == 3  # 1 draft + 2 revisions, then stop


def test_judge_failures_feed_the_revision_too() -> None:
    flaky = doc([para("The defendant undertook to vacate the premises.", PLAINT_CITE)])
    fixed = doc([para("The defendant agreed to pay the balance.", PLAINT_CITE)])
    model = SeqDraftModel([flaky, fixed])
    judge = RecordingJudge(reject_containing="undertook to vacate")

    draft, run = generate_draft("m1", DraftType.LEGAL_NOTICE, CHUNKS, model, judge=judge)
    assert run.judge == "RecordingJudge"
    assert [len(r) for r in run.rounds] == [1, 0]
    assert "does not establish this claim" in model.calls[1][1]
    assert draft.paragraphs[0].verified is True


def test_converged_drafts_list_every_placeholder_in_missing_info() -> None:
    d = doc([para("Notice is addressed to [● name of noticee] at [● address].")])
    model = SeqDraftModel([d])
    draft, _ = generate_draft("m1", DraftType.LEGAL_NOTICE, CHUNKS, model)
    assert "[● name of noticee]" in draft.missing_info
    assert "[● address]" in draft.missing_info


# -- the derived List of Dates ------------------------------------------------------


def artifacts(*events: ChronologyEvent) -> MatterArtifacts:
    return MatterArtifacts(matter_id="m1", chronology=list(events))


def test_list_of_dates_is_sorted_with_an_explicit_undated_bucket() -> None:
    entries, violations = derive_list_of_dates(
        [
            ChronologyEvent(
                event_date=date(2023, 8, 14), event="Listed for hearing", cites=ORDER_CITE
            ),
            ChronologyEvent(event="Possession withheld", cites=PLAINT_CITE),
            ChronologyEvent(
                event_date=date(2019, 3, 12), event="Sale deed executed", cites=PLAINT_CITE
            ),
        ],
        CHUNKS,
    )
    assert violations == []
    assert [e.event for e in entries] == [
        "Sale deed executed",
        "Listed for hearing",
        "Possession withheld",  # undated, last, never interleaved by guesswork
    ]
    assert entries[2].event_date is None


def test_stale_chronology_is_repaired_and_reported_not_trusted() -> None:
    """Artifacts were verified against the record as it was THEN. Deriving
    re-checks against the chunks as they are NOW."""
    entries, violations = derive_list_of_dates(
        [
            # its document has since been removed from the matter
            ChronologyEvent(
                event_date=date(2020, 1, 1),
                event="Reply sent",
                cites=[Citation(file="deleted.pdf", page=1)],
            ),
            # the cited page no longer states this date
            ChronologyEvent(
                event_date=date(2019, 3, 13), event="Sale deed executed", cites=PLAINT_CITE
            ),
        ],
        CHUNKS,
    )
    assert [e.event for e in entries] == ["Sale deed executed"]
    assert entries[0].event_date is None  # nulled into the undated bucket
    assert {v.kind for v in violations} == {"unresolvable_citation", "unsupported_date"}


# -- synopsis & list of dates -------------------------------------------------------


def synopsis_draft(paragraphs: list[DraftParagraph], **kw) -> DraftDocument:
    return DraftDocument(
        matter_id="m1",
        doc_type=DraftType.SYNOPSIS_LOD,
        title="SYNOPSIS AND LIST OF DATES",
        synopsis=paragraphs,
        **kw,
    )


CHRONOLOGY = [
    ChronologyEvent(event_date=date(2019, 3, 12), event="Sale deed executed", cites=PLAINT_CITE),
    ChronologyEvent(event_date=date(2024, 1, 5), event="Appeal dismissed", cites=JUDGMENT_CITE),
]


def test_composed_types_require_artifacts() -> None:
    model = SeqDraftModel([synopsis_draft([])])
    with pytest.raises(ValueError, match="generate the case brief"):
        generate_draft("m1", DraftType.SYNOPSIS_LOD, CHUNKS, model)


def test_the_list_of_dates_is_derived_never_drafted() -> None:
    """Whatever the model puts in list_of_dates is discarded and replaced by
    the rendering of the verified chronology."""
    invented = ListOfDatesEntry(
        event_date=date(1203, 1, 19),
        event="An invented row",
        cites=[Citation(file="nowhere.pdf", page=9)],
    )
    model = SeqDraftModel(
        [
            synopsis_draft(
                [para("The sale deed was executed on 12.03.2019.", PLAINT_CITE)],
                list_of_dates=[invented],
            )
        ]
    )
    draft, run = generate_draft(
        "m1",
        DraftType.SYNOPSIS_LOD,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
    )
    assert [e.event for e in draft.list_of_dates] == ["Sale deed executed", "Appeal dismissed"]
    assert run.attempts == 1  # the invented row never even reached verification
    # and the model was told the verified list, not asked for one
    assert "VERIFIED LIST OF DATES" in model.calls[0][1]
    assert "12.03.2019 — Sale deed executed" in model.calls[0][1]


def test_synopsis_prose_written_under_paragraphs_is_relabelled() -> None:
    model = SeqDraftModel(
        [
            DraftDocument(
                matter_id="m1",
                doc_type=DraftType.SYNOPSIS_LOD,
                title="SYNOPSIS AND LIST OF DATES",
                paragraphs=[para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)],
            )
        ]
    )
    draft, _ = generate_draft(
        "m1",
        DraftType.SYNOPSIS_LOD,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
    )
    assert draft.paragraphs == []
    assert len(draft.synopsis) == 1 and draft.synopsis[0].verified is True


def test_synopsis_paragraphs_go_through_the_full_loop() -> None:
    bad = synopsis_draft([para("The appeal was dismissed on 06.01.2024.", JUDGMENT_CITE)])
    good = synopsis_draft([para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)])
    model = SeqDraftModel([bad, good])
    draft, run = generate_draft(
        "m1",
        DraftType.SYNOPSIS_LOD,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
    )
    assert run.attempts == 2
    assert draft.synopsis[0].text.startswith("The appeal was dismissed on 05.01.2024")


# -- the SLP consumes its components --------------------------------------------------


def slp_body() -> DraftDocument:
    return DraftDocument(
        matter_id="m1",
        doc_type=DraftType.SLP,
        title="SPECIAL LEAVE PETITION",
        court_header="IN THE SUPREME COURT OF INDIA",
        paragraphs=[
            para("QUESTIONS OF LAW", kind="heading"),
            para(
                "Whether the appeal could be dismissed as it was on 05.01.2024?",
                JUDGMENT_CITE,
                kind="ground",
            ),
            para("GROUNDS", kind="heading"),
            para(
                "Because the judgment dated 05.01.2024 affirmed the decree without "
                "considering the sale deed dated 12.03.2019.",
                JUDGMENT_CITE + PLAINT_CITE,
                kind="ground",
            ),
        ],
        prayer=["Grant special leave to appeal against the impugned judgment."],
    )


def test_the_slp_generates_and_embeds_its_components() -> None:
    component = synopsis_draft([para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)])
    model = SeqDraftModel([component, slp_body()])
    draft, run = generate_draft(
        "m1",
        DraftType.SLP,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
    )

    assert len(model.calls) == 2  # one for the synopsis, one for the petition body
    assert "VERIFIED SYNOPSIS" in model.calls[1][1]
    assert draft.doc_type == DraftType.SLP
    assert [e.event for e in draft.list_of_dates] == ["Sale deed executed", "Appeal dismissed"]
    assert draft.synopsis and draft.synopsis[0].text.startswith("The appeal was dismissed")
    assert draft.paragraphs[1].kind == "ground"
    assert run.component is not None and run.component.attempts == 1
    assert run.total_attempts == 2


def test_the_slp_reuses_a_provided_synopsis_component() -> None:
    component, _ = generate_draft(
        "m1",
        DraftType.SYNOPSIS_LOD,
        CHUNKS,
        SeqDraftModel(
            [synopsis_draft([para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)])]
        ),
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
    )
    model = SeqDraftModel([slp_body()])
    draft, run = generate_draft(
        "m1",
        DraftType.SLP,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
        synopsis_component=component,
    )
    assert len(model.calls) == 1  # no second synopsis generation
    assert run.component is None
    assert draft.list_of_dates == component.list_of_dates
    assert draft.synopsis == component.synopsis


def test_a_reused_component_list_of_dates_is_repaired_against_the_current_record() -> None:
    """A saved synopsis was verified against the record as it was THEN. Reuse
    re-runs the staleness repair — copying it through unchecked would stamp
    unfixable violations into every loop round. Found by review."""
    component = synopsis_draft(
        [para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)]
    ).model_copy(
        update={
            "list_of_dates": [
                ListOfDatesEntry(
                    event_date=date(2019, 3, 13),  # the cited page says 12.03.2019
                    event="Sale deed executed",
                    cites=PLAINT_CITE,
                )
            ]
        }
    )
    model = SeqDraftModel([slp_body()])
    draft, run = generate_draft(
        "m1",
        DraftType.SLP,
        CHUNKS,
        model,
        artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
        synopsis_component=component,
    )
    assert len(model.calls) == 1  # repaired and reported, not looped to death
    assert draft.list_of_dates[0].event_date is None  # the undated bucket
    assert [v.kind for v in run.lod_violations] == ["unsupported_date"]


def test_a_stale_reused_component_fails_before_any_model_call() -> None:
    """The component's prose is stamped onto every round, so the model can
    never repair it. If it no longer verifies, fail NOW — not after burning
    1 + MAX_REVISIONS model calls on a loop that cannot converge."""
    component = synopsis_draft(
        [para("Reply was sent by the respondent.", [Citation(file="deleted.pdf", page=1)])]
    )
    model = SeqDraftModel([slp_body()])
    with pytest.raises(ValueError, match="no longer verifies against the record"):
        generate_draft(
            "m1",
            DraftType.SLP,
            CHUNKS,
            model,
            artifacts=MatterArtifacts(matter_id="m1", chronology=CHRONOLOGY),
            synopsis_component=component,
        )
    assert model.calls == []


# -- the judge seam ------------------------------------------------------------------


def test_support_judge_defaults_off_for_local_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """LAWSCHOOL_LLM=ollama exists to run without an API key; the free layers
    still stand, and the support layer switches off rather than crashing."""
    from pipeline.drafting import get_support_judge

    monkeypatch.setenv("LAWSCHOOL_LLM", "ollama")
    monkeypatch.delenv("LAWSCHOOL_SUPPORT_JUDGE", raising=False)
    assert get_support_judge() is None


def test_support_judge_is_selectable_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline.drafting import get_support_judge
    from pipeline.evals.judge import LexicalJudge

    monkeypatch.setenv("LAWSCHOOL_SUPPORT_JUDGE", "lexical")
    assert isinstance(get_support_judge(), LexicalJudge)
    monkeypatch.setenv("LAWSCHOOL_SUPPORT_JUDGE", "none")
    assert get_support_judge() is None


# -- new paragraph kinds through the old gate ------------------------------------------


def test_grounds_carry_the_citation_duty_and_headings_do_not() -> None:
    d = doc(
        [
            para("GROUNDS", kind="heading"),
            para("Because the decree ignores the record.", kind="ground"),  # uncited
        ]
    )
    clean, violations = validate_draft(d, CHUNKS)
    assert clean.paragraphs[0].verified is True  # a heading asserts nothing
    assert clean.paragraphs[1].verified is False
    assert [v.kind for v in violations] == ["uncited_factual_paragraph"]


def test_paperbook_docx_export() -> None:
    entries, _ = derive_list_of_dates(CHRONOLOGY, CHUNKS)
    draft = slp_body().model_copy(
        update={
            "synopsis": [para("The appeal was dismissed on 05.01.2024.", JUDGMENT_CITE)],
            "list_of_dates": entries,
            "missing_info": ["[● date of receipt of certified copy]"],
        }
    )
    blob = draft_to_docx(draft)
    assert blob[:2] == b"PK"
    assert len(blob) > 1000
