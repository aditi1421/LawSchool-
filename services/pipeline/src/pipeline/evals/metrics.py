"""Metrics: citation accuracy, fabrication count, chronology recall.

Definitions (see evals/README.md):
- citation accuracy: fraction of generated claims whose cited page supports them
- fabrication count: generated claims supported by NO page in the record
  (a wrong cite that some other page supports is a citation error, not a
  fabrication; a claim no page supports is a fabrication)
- chronology recall: fraction of gold events recovered by the generated chronology

Both judgements are pluggable: a SupportJudge answers "does this page support
this claim?", an EventMatcher answers "is this the same event?". Defaults are
the offline lexical ones so existing callers and tests are unaffected; the real
gate passes the Claude-backed pair. Independent judgements run on a bounded
thread pool — the judges are I/O-bound and cache their own verdicts.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

from pipeline.evals.gold import GoldEvent, GoldMatter
from pipeline.evals.judge import SupportJudge
from pipeline.evals.matcher import EventMatcher, LexicalEventMatcher
from pipeline.models import Citation, ChronologyEvent, MatterArtifacts

MAX_WORKERS = 8

T = TypeVar("T")
R = TypeVar("R")

# page texts for one matter: {(file, page): text}
PageTexts = dict[tuple[str, int], str]


@dataclass
class ClaimAudit:
    claim: str
    cite: Citation
    cited_page_supports: bool
    supported_anywhere: bool


@dataclass
class EvalReport:
    matter_id: str
    citation_accuracy: float
    fabrication_count: int
    chronology_recall: float
    audits: list[ClaimAudit] = field(default_factory=list)
    missed_gold_events: list[GoldEvent] = field(default_factory=list)


def _claims(artifacts: MatterArtifacts) -> list[tuple[str, Citation]]:
    """Flatten every cited factual claim in the artifacts to (text, first-cite)."""
    out: list[tuple[str, Citation]] = []
    for ev in artifacts.chronology:
        for cite in ev.cites:
            out.append((ev.event, cite))
    for order in artifacts.proceedings:
        for cite in order.cites:
            out.append((order.direction, cite))
    for cont in artifacts.contentions:
        for side in (cont.petitioner, cont.respondent):
            if side is not None:
                for cite in side.cites:
                    out.append((side.position, cite))
    for issue in artifacts.issues:
        for cite in issue.cites:
            out.append((issue.text, cite))
    return out


def _event_matches(
    gold: GoldEvent,
    generated: ChronologyEvent,
    matcher: EventMatcher | None = None,
) -> bool:
    """A gold event is recovered if the dates agree exactly and the matcher agrees.

    The date check stays in code and runs first: a date mismatch is never a
    match, whatever the matcher would say, and only same-date candidates are
    worth an LLM call.
    """
    if gold.event_date != generated.event_date:
        return False
    if matcher is None:
        matcher = LexicalEventMatcher()
    return matcher.matches(gold.description, generated)


def _map(fn: Callable[[T], R], items: Sequence[T], max_workers: int) -> list[R]:
    """Map over independent judgements, bounded. Order-preserving."""
    if max_workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(fn, items))


def score_matter(
    gold: GoldMatter,
    artifacts: MatterArtifacts,
    pages: PageTexts,
    judge: SupportJudge,
    matcher: EventMatcher | None = None,
    max_workers: int = MAX_WORKERS,
) -> EvalReport:
    if matcher is None:
        matcher = LexicalEventMatcher()

    def audit(claim_cite: tuple[str, Citation]) -> ClaimAudit:
        claim, cite = claim_cite
        cited_text = pages.get((cite.file, cite.page), "")
        cited_ok = bool(cited_text) and judge.supports(claim, cited_text)
        # Only a claim whose own cite failed needs the full-record sweep, and
        # that sweep short-circuits on the first supporting page.
        anywhere = cited_ok or any(judge.supports(claim, text) for text in pages.values())
        return ClaimAudit(
            claim=claim,
            cite=cite,
            cited_page_supports=cited_ok,
            supported_anywhere=anywhere,
        )

    audits = _map(audit, _claims(artifacts), max_workers)

    citation_accuracy = (
        sum(a.cited_page_supports for a in audits) / len(audits) if audits else 1.0
    )
    fabrication_count = sum(not a.supported_anywhere for a in audits)

    def recovered(g: GoldEvent) -> bool:
        return any(_event_matches(g, ev, matcher) for ev in artifacts.chronology)

    missed = [
        g for g, found in zip(gold.events, _map(recovered, gold.events, max_workers)) if not found
    ]
    recall = 1.0 - (len(missed) / len(gold.events)) if gold.events else 1.0

    return EvalReport(
        matter_id=gold.matter_id,
        citation_accuracy=citation_accuracy,
        fabrication_count=fabrication_count,
        chronology_recall=recall,
        audits=audits,
        missed_gold_events=missed,
    )
