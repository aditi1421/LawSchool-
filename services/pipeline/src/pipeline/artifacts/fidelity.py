"""Fidelity: do the dates and amounts a claim asserts actually appear in the
page it cites?

This exists because of a measured failure, not a hypothetical one. Generating a
chronology from a plaint, llama3.2 emitted:

    event:      "Registered sale deed executed"
    cites:      plaint.pdf p.1 para 2      <- correct page, correct paragraph
    event_date: 1203-01-19                 <- the source says 12.03.2019

Every existing check passed. `validate_grounding` asks whether the citation
*resolves* to a real page; it does. An LLM judge reading the claim text would
not have caught it either, because the date lives in a separate field and never
appears in the sentence being judged.

Traceability is not accuracy. Resolution proves the page exists; support proves
the page says something like this; only fidelity proves the *specific figures*
are the record's own. Dates and money are where a legal document is wrong
catastrophically and silently — and, being literal strings, they are the part a
machine can check exactly.

Cheap by construction: pure code, no model, no API. It runs identically for
Claude, a local model, or anything else — which matters, because the weaker the
model, the more it is needed.

What happens to an unsupported figure:

- An unsupported **date** nulls to the undated bucket and the event survives.
  The event is usually real and only its date invented; "undated" is exactly
  the honest place for a date the record does not state, and the existing rule
  is already that dates are never inferred.
- An unsupported **amount** removes the claim. The figure is inside the prose,
  so there is nothing to null — and a sentence asserting a rupee amount the
  record never states is not repairable, only wrong.

Both are reported either way; silence would be the actual failure.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pipeline.models import Chunk, Citation, MatterArtifacts
from pipeline.structure.amounts import amounts_in, format_indian
from pipeline.structure.dates import extract_dates

# page -> the text the record actually has there
PageText = dict[tuple[str, int], str]


@dataclass
class FidelityViolation:
    kind: Literal["unsupported_date", "unsupported_amount"]
    artifact: str
    claim: str
    # The figure the model asserted that the cited page does not contain.
    asserted: str
    cites: list[Citation]


def page_texts(chunks: list[Chunk]) -> PageText:
    """Reassemble each cited page from its chunks.

    Fidelity is judged against the whole page, not the single cited paragraph:
    a date stated in the page's heading and referred to in its body is the
    record's own date, and failing that would punish correct work. The
    paragraph-level citation is still what the reader clicks through to.
    """
    pages: PageText = {}
    for c in chunks:
        key = (c.location.file, c.location.page)
        pages[key] = f"{pages.get(key, '')}\n{c.text}".strip()
    return pages


# These three are the fidelity machinery itself, shared with drafting
# (pipeline.drafting.verify) — one date parser, one amount parser, one
# definition of "the record states it", everywhere.


def cited_text(cites: list[Citation], pages: PageText) -> str:
    return "\n".join(pages.get((c.file, c.page), "") for c in cites)


def date_supported(value: date, cites: list[Citation], pages: PageText) -> bool:
    text = cited_text(cites, pages)
    return value in {d.value for d in extract_dates(text)}


def unsupported_amounts(claim: str, cites: list[Citation], pages: PageText) -> set[int]:
    """Amounts the claim asserts that its cited pages never state."""
    asserted = amounts_in(claim)
    if not asserted:
        return set()
    return asserted - amounts_in(cited_text(cites, pages))


def validate_fidelity(
    artifacts: MatterArtifacts, chunks: list[Chunk]
) -> tuple[MatterArtifacts, list[FidelityViolation]]:
    """Strip figures the record does not support. Run after validate_grounding,
    which has already removed claims whose citations do not resolve."""
    pages = page_texts(chunks)
    violations: list[FidelityViolation] = []

    chronology = []
    for ev in artifacts.chronology:
        bad_amounts = unsupported_amounts(ev.event, ev.cites, pages)
        if bad_amounts:
            violations.append(
                FidelityViolation(
                    kind="unsupported_amount",
                    artifact="chronology",
                    claim=ev.event[:160],
                    asserted=", ".join(f"Rs. {format_indian(a)}" for a in sorted(bad_amounts)),
                    cites=ev.cites,
                )
            )
            continue  # the figure is in the prose; there is nothing to repair

        if ev.event_date is not None and not date_supported(ev.event_date, ev.cites, pages):
            violations.append(
                FidelityViolation(
                    kind="unsupported_date",
                    artifact="chronology",
                    claim=ev.event[:160],
                    asserted=ev.event_date.isoformat(),
                    cites=ev.cites,
                )
            )
            ev = ev.model_copy(update={"event_date": None})  # the undated bucket
        chronology.append(ev)

    proceedings = []
    for order in artifacts.proceedings:
        bad_amounts = unsupported_amounts(order.direction, order.cites, pages)
        if bad_amounts:
            violations.append(
                FidelityViolation(
                    kind="unsupported_amount",
                    artifact="proceedings",
                    claim=order.direction[:160],
                    asserted=", ".join(f"Rs. {format_indian(a)}" for a in sorted(bad_amounts)),
                    cites=order.cites,
                )
            )
            continue
        update: dict = {}
        for field in ("order_date", "next_date"):
            value = getattr(order, field)
            if value is not None and not date_supported(value, order.cites, pages):
                violations.append(
                    FidelityViolation(
                        kind="unsupported_date",
                        artifact=f"proceedings.{field}",
                        claim=order.direction[:160],
                        asserted=value.isoformat(),
                        cites=order.cites,
                    )
                )
                update[field] = None
        proceedings.append(order.model_copy(update=update) if update else order)

    contentions = []
    for cont in artifacts.contentions:
        sides: dict = {}
        for name in ("petitioner", "respondent"):
            side = getattr(cont, name)
            if side is None:
                continue
            bad = unsupported_amounts(side.position, side.cites, pages)
            if bad:
                violations.append(
                    FidelityViolation(
                        kind="unsupported_amount",
                        artifact=f"contentions.{name}",
                        claim=side.position[:160],
                        asserted=", ".join(f"Rs. {format_indian(a)}" for a in sorted(bad)),
                        cites=side.cites,
                    )
                )
                sides[name] = None
        cont = cont.model_copy(update=sides) if sides else cont
        # A contention with neither side left asserts nothing.
        if cont.petitioner is not None or cont.respondent is not None:
            contentions.append(cont)

    return (
        artifacts.model_copy(
            update={
                "chronology": chronology,
                "proceedings": proceedings,
                "contentions": contentions,
            }
        ),
        violations,
    )
