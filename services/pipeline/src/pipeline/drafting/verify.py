"""Draft verification: the three layers, applied to court documents.

Layer      | asks                                                    | cost
-----------|---------------------------------------------------------|------
resolution | does every citation point at a real page?               | free
fidelity   | do the dates/amounts asserted occur on the cited page?  | free
support    | does the page genuinely establish the claim?            | LLM call

Resolution and fidelity are pure code and run on every paragraph — they
protect a weak local model exactly as much as Claude. Support costs an API
call per claim, so it is RISK-GATED: only paragraphs that assert a date, a
rupee amount, or a party's obligation are judged (see `assertion_risks`).
Boilerplate, prayer language and verification clauses are never judged.

The fidelity machinery is pipeline.artifacts.fidelity's — same date parser,
same amount parser, same definition of "the record states it". Drafting adds
only the mapping from a DraftDocument's shape onto those checks.

`verify_draft` is a pure check: it mutates nothing and returns what failed.
Repair is the model's job — the verify-and-revise loop in generate.py shows
the model each specific failure. `validate_draft` (the original single-shot
gate) keeps its repairing behaviour: strip what does not resolve, recompute
`verified`, report.
"""

import os
import re
from dataclasses import dataclass
from typing import Literal

from pipeline.artifacts.fidelity import (
    cited_text,
    date_supported,
    page_texts,
    unsupported_amounts,
)
from pipeline.drafting.models import DraftDocument, DraftParagraph
from pipeline.evals.judge import SupportJudge, make_judge
from pipeline.models import Chunk, Citation
from pipeline.structure.amounts import amounts_in, extract_amounts, format_indian
from pipeline.structure.dates import extract_dates

PLACEHOLDER_MARK = "[●"

# A placeholder marks a gap, not an assertion — figures inside one ("[● date
# of receipt of certified copy]") are the very thing the draft is NOT
# claiming, so they are cut before any date/amount/risk analysis.
_PLACEHOLDER_RE = re.compile(r"\[●[^\]]*\]")


def asserted_text(text: str) -> str:
    """The paragraph's actual assertions: its text minus placeholder gaps."""
    return _PLACEHOLDER_RE.sub(" ", text)


# -- the risk test -------------------------------------------------------------

# Support judging is risk-only, and "risky" is a testable predicate, not a
# vibe: a paragraph is judged iff it asserts a date, a rupee amount, or a
# party's obligation. Obligation language is matched lexically — the words a
# pleading uses when it says someone was bound to do something.
_OBLIGATION = re.compile(
    r"\b("
    r"shall|liable|bound to|obliged|obligated|"
    r"undertakes?|undertook|undertaken|agreed to|covenants?|covenanted|"
    r"directed to|payable|duty to|dut(y|ies) of|owes?|owed|"
    r"responsible for|indemnif\w*|defaulted|breach(es|ed)?"
    r")\b",
    re.IGNORECASE,
)

RiskTag = Literal["date", "amount", "obligation"]


def assertion_risks(text: str) -> frozenset[RiskTag]:
    """Why this text is risky enough to spend a judge call on — empty when it
    asserts no date, no amount, and no obligation."""
    bare = asserted_text(text)
    risks: set[RiskTag] = set()
    if extract_dates(bare):
        risks.add("date")
    if extract_amounts(bare):
        risks.add("amount")
    if _OBLIGATION.search(bare):
        risks.add("obligation")
    return frozenset(risks)


# -- violations ----------------------------------------------------------------


@dataclass
class DraftViolation:
    kind: Literal[
        "unresolvable_citation",
        "uncited_factual_paragraph",
        "unsupported_date",
        "unsupported_amount",
        "unsupported_claim",
    ]
    paragraph: str  # excerpt of the offending text
    cite: Citation | None = None
    # The figure the draft asserts that the cited page does not contain
    # (fidelity), or the risk tags that triggered judging (support).
    asserted: str | None = None
    # Address inside the document — "synopsis[2]", "paragraphs[5]",
    # "list_of_dates[3]", "prayer[1]" — so a failure names its paragraph.
    where: str | None = None


# -- layer 1: resolution (the original single-shot gate, kept) ------------------


def validate_draft(
    draft: DraftDocument, chunks: list[Chunk]
) -> tuple[DraftDocument, list[DraftViolation]]:
    """Code-enforced honesty for drafts — the repairing form.

    - Citations that don't resolve to the record are stripped and reported;
      the paragraph text stays (drafts are work product) but is left unverified.
    - A factual/ground paragraph with no surviving citation and no [●]
      placeholder is reported: it asserts matter facts with no traceable source.
    - `verified` is recomputed here; whatever the model set is discarded.
    """
    pages = {(c.location.file, c.location.page) for c in chunks}
    violations: list[DraftViolation] = []

    def check(section: str, paras: list[DraftParagraph]) -> list[DraftParagraph]:
        out = []
        for i, para in enumerate(paras):
            resolved = []
            for cite in para.cites:
                if (cite.file, cite.page) in pages:
                    resolved.append(cite)
                else:
                    violations.append(
                        DraftViolation(
                            kind="unresolvable_citation",
                            paragraph=para.text[:120],
                            cite=cite,
                            where=f"{section}[{i}]",
                        )
                    )
            if para.kind in ("boilerplate", "heading"):
                verified = len(resolved) == len(para.cites)
            else:  # factual and ground paragraphs carry the citation duty
                has_placeholder = PLACEHOLDER_MARK in para.text
                if not resolved and not has_placeholder:
                    violations.append(
                        DraftViolation(
                            kind="uncited_factual_paragraph",
                            paragraph=para.text[:120],
                            where=f"{section}[{i}]",
                        )
                    )
                    verified = False
                else:
                    verified = len(resolved) == len(para.cites) and (
                        bool(resolved) or has_placeholder
                    )
            out.append(para.model_copy(update={"cites": resolved, "verified": verified}))
        return out

    return (
        draft.model_copy(
            update={
                "synopsis": check("synopsis", draft.synopsis),
                "paragraphs": check("paragraphs", draft.paragraphs),
            }
        ),
        violations,
    )


# -- layers 1+2+3 together: the loop's pure check --------------------------------


def verify_draft(
    draft: DraftDocument,
    chunks: list[Chunk],
    judge: SupportJudge | None = None,
) -> list[DraftViolation]:
    """Everything wrong with this draft, located and named. Mutates nothing —
    in the loop, repair belongs to the model, which is shown each failure.

    Convergence means this returns []. With a judge, support runs only on
    risky paragraphs that already passed resolution and fidelity (judging a
    claim against pages it does not even cite would waste the call).
    """
    pages = page_texts(chunks)
    violations: list[DraftViolation] = []

    for section, i, para in draft.body_paragraphs():
        where = f"{section}[{i}]"
        if para.kind == "heading":
            continue
        clean = True

        for cite in para.cites:
            if (cite.file, cite.page) not in pages:
                violations.append(
                    DraftViolation(
                        kind="unresolvable_citation",
                        paragraph=para.text[:120],
                        cite=cite,
                        where=where,
                    )
                )
                clean = False

        if para.kind == "boilerplate":
            continue

        has_placeholder = PLACEHOLDER_MARK in para.text
        if not para.cites and not has_placeholder:
            violations.append(
                DraftViolation(
                    kind="uncited_factual_paragraph", paragraph=para.text[:120], where=where
                )
            )
            clean = False

        # Fidelity: every figure the paragraph asserts must occur on a page it
        # cites. A paragraph with no citations has no supported figures at all
        # — a specific date or amount can never ride in on a placeholder.
        bare = asserted_text(para.text)
        for mention in extract_dates(bare):
            if not date_supported(mention.value, para.cites, pages):
                violations.append(
                    DraftViolation(
                        kind="unsupported_date",
                        paragraph=para.text[:120],
                        asserted=mention.raw,
                        where=where,
                    )
                )
                clean = False
        for amount in sorted(unsupported_amounts(bare, para.cites, pages)):
            violations.append(
                DraftViolation(
                    kind="unsupported_amount",
                    paragraph=para.text[:120],
                    asserted=f"Rs. {format_indian(amount)}",
                    where=where,
                )
            )
            clean = False

        # Support, risk-gated: an API call is spent only on a paragraph that
        # asserts a date, an amount, or an obligation — and only once the free
        # layers pass, so the judge reads the pages the claim actually cites.
        if judge is not None and clean and para.cites:
            risks = assertion_risks(para.text)
            if risks and not judge.supports(bare.strip(), cited_text(para.cites, pages)):
                violations.append(
                    DraftViolation(
                        kind="unsupported_claim",
                        paragraph=para.text[:120],
                        asserted=", ".join(sorted(risks)),
                        where=where,
                    )
                )

    # The List of Dates is derived in code from the verified chronology, so
    # by construction it should pass — this re-check is the guarantee that it
    # did, against the chunks as they are NOW (documents can be deleted
    # between artifact generation and drafting).
    for i, entry in enumerate(draft.list_of_dates):
        where = f"list_of_dates[{i}]"
        unresolved = [c for c in entry.cites if (c.file, c.page) not in pages]
        for cite in unresolved:
            violations.append(
                DraftViolation(
                    kind="unresolvable_citation",
                    paragraph=entry.event[:120],
                    cite=cite,
                    where=where,
                )
            )
        if unresolved:
            continue
        if entry.event_date is not None and not date_supported(
            entry.event_date, entry.cites, pages
        ):
            violations.append(
                DraftViolation(
                    kind="unsupported_date",
                    paragraph=entry.event[:120],
                    asserted=entry.event_date.isoformat(),
                    where=where,
                )
            )
        for amount in sorted(unsupported_amounts(entry.event, entry.cites, pages)):
            violations.append(
                DraftViolation(
                    kind="unsupported_amount",
                    paragraph=entry.event[:120],
                    asserted=f"Rs. {format_indian(amount)}",
                    where=where,
                )
            )

    # Free-text fields — title, cause-title block, prayer items — cannot carry
    # citations, but an invented figure in any of them is still an invented
    # figure. Checked against the whole record: the weakest support that is
    # still support. (Found by review: a date the model wrote into the court
    # header used to sail through every layer.)
    record_text = "\n".join(pages.values())
    record_dates = {d.value for d in extract_dates(record_text)}
    record_amounts = amounts_in(record_text)
    free_text = [("title", draft.title), ("court_header", draft.court_header or "")] + [
        (f"prayer[{i}]", relief) for i, relief in enumerate(draft.prayer)
    ]
    for where, text in free_text:
        bare = asserted_text(text)
        for mention in extract_dates(bare):
            if mention.value not in record_dates:
                violations.append(
                    DraftViolation(
                        kind="unsupported_date",
                        paragraph=text[:120],
                        asserted=mention.raw,
                        where=where,
                    )
                )
        for amount in sorted(amounts_in(bare) - record_amounts):
            violations.append(
                DraftViolation(
                    kind="unsupported_amount",
                    paragraph=text[:120],
                    asserted=f"Rs. {format_indian(amount)}",
                    where=where,
                )
            )

    return violations


# -- the judge seam --------------------------------------------------------------


def get_support_judge() -> SupportJudge | None:
    """The support judge the product loop runs with.

    LAWSCHOOL_SUPPORT_JUDGE = claude | lexical | none. Default: claude — the
    judge promoted from the eval harness (already disk-cached, tuned to accept
    paraphrase and reject the plausible-but-unstated). When the drafting
    provider is a local model (LAWSCHOOL_LLM=ollama), default to none instead:
    that configuration exists to run without an API key, and the free layers
    still stand. None disables the support layer only.
    """
    kind = os.environ.get("LAWSCHOOL_SUPPORT_JUDGE")
    if kind is None:
        provider = os.environ.get("LAWSCHOOL_LLM", "claude").lower()
        kind = "none" if provider == "ollama" else "claude"
    kind = kind.lower()
    if kind == "none":
        return None
    return make_judge(kind)
