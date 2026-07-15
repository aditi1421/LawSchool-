"""Metrics: citation accuracy, fabrication count, chronology recall.

Definitions (see evals/README.md):
- citation accuracy: fraction of generated claims whose cited page supports them
- fabrication count: generated claims supported by NO page in the record
  (a wrong cite that some other page supports is a citation error, not a
  fabrication; a claim no page supports is a fabrication)
- chronology recall: fraction of gold events recovered by the generated chronology
"""

from dataclasses import dataclass, field

from pipeline.evals.gold import GoldEvent, GoldMatter
from pipeline.evals.judge import SupportJudge, _tokens, _STOPWORDS
from pipeline.models import Citation, ChronologyEvent, MatterArtifacts

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


def _event_matches(gold: GoldEvent, generated: ChronologyEvent) -> bool:
    """A gold event is recovered if dates agree and descriptions overlap."""
    if gold.event_date != generated.event_date:
        return False
    gold_tokens = _tokens(gold.description) - _STOPWORDS
    gen_tokens = _tokens(generated.event) - _STOPWORDS
    if not gold_tokens:
        return False
    return len(gold_tokens & gen_tokens) / len(gold_tokens) >= 0.5


def score_matter(
    gold: GoldMatter,
    artifacts: MatterArtifacts,
    pages: PageTexts,
    judge: SupportJudge,
) -> EvalReport:
    audits: list[ClaimAudit] = []
    for claim, cite in _claims(artifacts):
        cited_text = pages.get((cite.file, cite.page), "")
        cited_ok = bool(cited_text) and judge.supports(claim, cited_text)
        anywhere = cited_ok or any(judge.supports(claim, text) for text in pages.values())
        audits.append(
            ClaimAudit(
                claim=claim,
                cite=cite,
                cited_page_supports=cited_ok,
                supported_anywhere=anywhere,
            )
        )

    citation_accuracy = (
        sum(a.cited_page_supports for a in audits) / len(audits) if audits else 1.0
    )
    fabrication_count = sum(not a.supported_anywhere for a in audits)

    missed = [
        g
        for g in gold.events
        if not any(_event_matches(g, ev) for ev in artifacts.chronology)
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
