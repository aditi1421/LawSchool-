"""The drafting agent: record -> grounded court document.

Since the verify-and-revise upgrade, generation is a loop, not a shot:

    draft -> verify (resolution + fidelity + risk-gated support)
          -> revise what failed, showing the model each specific failure
          -> re-verify -> produce

The loop is bounded. A draft that cannot converge fails LOUDLY — it raises
DraftDidNotConverge with the surviving failures named; it never silently
returns its best guess. Verification is pure code plus an optional support
judge, so the guarantee holds for any provider; the weaker the model, the
more revision rounds it will need, not the less checking it will get.

Composition: the List of Dates is never drafted. It is derived here, in code,
from the matter's already-verified chronology (`derive_list_of_dates`), and
the SLP consumes the Synopsis and List of Dates as components — a re-derived
chronology could contradict the standalone one, which is worse than either
being wrong alone.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from pipeline.artifacts.fidelity import (
    cited_text,
    date_supported,
    page_texts,
    unsupported_amounts,
)
from pipeline.artifacts.generate import build_context
from pipeline.drafting.models import (
    COMPOSED_TYPES,
    DraftDocument,
    DraftType,
    ListOfDatesEntry,
)
from pipeline.drafting.prompts import DRAFT_SYSTEM, GUIDANCE
from pipeline.drafting.verify import (
    DraftViolation,
    validate_draft,
    verify_draft,
)
from pipeline.evals.judge import SupportJudge
from pipeline.llm import ClaudeLLM, StructuredLLM, get_llm
from pipeline.models import Chunk, ChronologyEvent, MatterArtifacts
from pipeline.structure.amounts import amounts_in, format_indian
from pipeline.structure.dates import extract_dates

MAX_REVISIONS = 2  # a draft gets 1 + MAX_REVISIONS model calls to come clean

_PLACEHOLDER_RE = re.compile(r"\[●[^\]]*\]")


class DraftModel(Protocol):
    def draft(self, system: str, user: str) -> DraftDocument: ...


class LLMDraftModel:
    """Drafting over any StructuredLLM. The verify loop enforces the honesty
    rules on the result regardless of provider."""

    def __init__(self, llm: StructuredLLM | None = None) -> None:
        self._llm = llm or get_llm()

    def draft(self, system: str, user: str) -> DraftDocument:
        return self._llm.generate(system, user, DraftDocument)


def AnthropicDraftModel(model: str = "claude-opus-4-8") -> LLMDraftModel:
    return LLMDraftModel(ClaudeLLM(model=model))


class DraftDidNotConverge(RuntimeError):
    """The bounded loop ran out of revisions with failures still standing.

    Raised, never swallowed: a draft the record does not verify must not
    reach a lawyer looking like one that does.
    """

    def __init__(self, doc_type: DraftType, attempts: int, violations: list[DraftViolation]):
        self.violations = violations
        details = "\n".join(
            f"  - {v.where or '?'}: {v.kind}"
            + (f" ({v.asserted})" if v.asserted else "")
            + f' — "{v.paragraph}"'
            for v in violations[:8]
        )
        more = f"\n  … and {len(violations) - 8} more" if len(violations) > 8 else ""
        super().__init__(
            f"The {doc_type.value} draft could not be verified against the record "
            f"after {attempts} attempts. Nothing was saved. Still failing:\n{details}{more}"
        )


@dataclass
class DraftRun:
    """What the loop did — for the job record and the reader who asks why a
    draft took three model calls."""

    attempts: int = 0  # model calls made for this document's own paragraphs
    rounds: list[list[DraftViolation]] = field(default_factory=list)  # failures per verify pass
    judge: str | None = None  # class name of the support judge, if one ran
    # Chronology entries repaired while deriving the List of Dates (stale
    # artifacts vs the current record) — code-repaired, reported, not loop-blocking.
    lod_violations: list[DraftViolation] = field(default_factory=list)
    component: "DraftRun | None" = None  # the synopsis run, when the SLP generated one

    @property
    def revised(self) -> int:
        return max(0, self.attempts - 1) + (self.component.revised if self.component else 0)

    @property
    def total_attempts(self) -> int:
        return self.attempts + (self.component.total_attempts if self.component else 0)


# -- the List of Dates: derived, never drafted -----------------------------------


def derive_list_of_dates(
    chronology: list[ChronologyEvent], chunks: list[Chunk]
) -> tuple[list[ListOfDatesEntry], list[DraftViolation]]:
    """Render the verified chronology as List of Dates entries.

    The chronology was grounding- and fidelity-checked when the artifacts were
    generated — but against the record as it was THEN. Documents can be
    removed afterwards, so every entry is re-checked against the chunks as
    they are now: an entry whose citation no longer resolves (or whose event
    text asserts an amount the page no longer supports) is dropped; a date the
    cited page no longer states is nulled into the undated bucket. All of it
    is reported — these repairs mean the saved brief is stale.
    """
    pages = page_texts(chunks)
    entries: list[ListOfDatesEntry] = []
    violations: list[DraftViolation] = []

    for ev in chronology:
        unresolved = [c for c in ev.cites if (c.file, c.page) not in pages]
        if unresolved:
            for cite in unresolved:
                violations.append(
                    DraftViolation(
                        kind="unresolvable_citation",
                        paragraph=ev.event[:120],
                        cite=cite,
                        where="chronology",
                    )
                )
            continue
        bad_amounts = sorted(unsupported_amounts(ev.event, ev.cites, pages))
        if bad_amounts:
            violations.append(
                DraftViolation(
                    kind="unsupported_amount",
                    paragraph=ev.event[:120],
                    asserted=", ".join(f"Rs. {format_indian(a)}" for a in bad_amounts),
                    where="chronology",
                )
            )
            continue
        event_date = ev.event_date
        if event_date is not None and not date_supported(event_date, ev.cites, pages):
            violations.append(
                DraftViolation(
                    kind="unsupported_date",
                    paragraph=ev.event[:120],
                    asserted=event_date.isoformat(),
                    where="chronology",
                )
            )
            event_date = None  # the undated bucket
        entries.append(
            ListOfDatesEntry(
                event_date=event_date,
                event=ev.event,
                cites=ev.cites,
                confidence=ev.confidence,
            )
        )

    # Dated entries in date order; undated entries keep their record order at
    # the end, under an explicit bucket — never interleaved by guesswork.
    dated = sorted(
        (e for e in entries if e.event_date is not None),
        key=lambda e: e.event_date or date.min,
    )
    undated = [e for e in entries if e.event_date is None]
    return dated + undated, violations


def render_list_of_dates(entries: list[ListOfDatesEntry]) -> str:
    """The List of Dates as text — for the model's context and for prompts."""
    lines = []
    for e in entries:
        when = e.event_date.strftime("%d.%m.%Y") if e.event_date else "Undated"
        cites = "; ".join(
            f"{c.file} p.{c.page}" + (f" ¶{c.para}" if c.para is not None else "") for c in e.cites
        )
        lines.append(f"{when} — {e.event} [{cites}]")
    return "\n".join(lines)


# -- the verify-and-revise loop ---------------------------------------------------


def _revision_user(
    base_user: str,
    draft: DraftDocument,
    violations: list[DraftViolation],
    chunks: list[Chunk],
) -> str:
    """The revision request: the same record, the model's own draft, and each
    failure named — with what the cited pages actually contain, so the fix is
    a correction, not a fresh guess."""
    pages = page_texts(chunks)
    lines: list[str] = []
    for n, v in enumerate(violations, 1):
        lines.append(f'{n}. {v.where or "?"} — {v.kind}: "{v.paragraph}"')
        if v.kind == "unresolvable_citation" and v.cite is not None:
            lines.append(
                f"   The citation [{v.cite.file} | page {v.cite.page}] does not exist in "
                f"the case file. Cite only locations copied exactly from the chunk tags."
            )
        elif v.kind == "uncited_factual_paragraph":
            lines.append(
                "   This paragraph asserts facts of the matter with no citation and no "
                "[●] placeholder. Cite the chunk(s) it draws from, or rewrite the "
                "unsourced specifics as [● description] placeholders."
            )
        elif v.kind in ("unsupported_date", "unsupported_amount"):
            if _WHERE_RE.match(v.where or ""):
                lines.append(
                    f"   The paragraph asserts {v.asserted}, which does not appear on "
                    f"the page(s) it cites."
                )
                found = _figures_on(v, draft, pages)
                if found:
                    lines.append(f"   {found}")
            else:
                # title / court_header / prayer — uncitable fields, checked
                # against the whole record.
                lines.append(
                    f"   This field asserts {v.asserted}, which does not appear "
                    "anywhere in the record. Take the figure from the record or "
                    "replace it with a [● description] placeholder."
                )
        elif v.kind == "unsupported_claim":
            lines.append(
                "   The cited page does not establish this claim (it asserts a "
                f"{v.asserted or 'risky fact'} the page does not state). Either cite the "
                "page that actually states it, restate the claim as what the page does "
                "say, or replace the unsupported part with a [● description] placeholder."
            )
    failures = "\n".join(lines)
    return (
        f"{base_user}\n\n"
        f"YOUR PREVIOUS DRAFT (JSON):\n{draft.model_dump_json(indent=None)}\n\n"
        f"VERIFICATION FAILURES — the record does not support these as written:\n"
        f"{failures}\n\n"
        "Produce the corrected COMPLETE document. Fix only what failed; keep every "
        "verified paragraph as it is. For each failure, either (a) cite the location "
        "that actually states the fact, (b) correct the figure to what the cited page "
        "states, or (c) replace the unsupported specific with a [● description] "
        "placeholder and list it in missing_info. Never invent a fact, a figure, or "
        "a citation to satisfy a failure."
    )


_WHERE_RE = re.compile(r"^(synopsis|paragraphs)\[(\d+)\]$")


def _figures_on(v: DraftViolation, draft: DraftDocument, pages) -> str:
    """What the failing paragraph's cited pages actually state — the model
    corrects against the record's own figures instead of guessing again."""
    m = _WHERE_RE.match(v.where or "")
    if not m:
        return ""
    paras = draft.synopsis if m.group(1) == "synopsis" else draft.paragraphs
    idx = int(m.group(2))
    if idx >= len(paras) or not paras[idx].cites:
        return "The paragraph cites nothing, so no figure in it can be supported."
    text = cited_text(paras[idx].cites, pages)
    if v.kind == "unsupported_date":
        found = sorted({d.raw for d in extract_dates(text)})
        return (
            f"The cited page(s) state these dates: {', '.join(found)}."
            if found
            else "The cited page(s) state no dates at all."
        )
    found = sorted(amounts_in(text))
    return (
        "The cited page(s) state these amounts: "
        + ", ".join(f"Rs. {format_indian(a)}" for a in found)
        + "."
        if found
        else "The cited page(s) state no rupee amounts at all."
    )


def _collect_missing_info(draft: DraftDocument) -> list[str]:
    """missing_info must list every [●] placeholder the text actually uses —
    the gaps are the point, and an unlisted gap is an unmarked one."""
    listed = "\n".join(draft.missing_info)
    found: list[str] = []
    texts = [p.text for _, _, p in draft.body_paragraphs()] + list(draft.prayer)
    if draft.court_header:
        texts.append(draft.court_header)
    texts.append(draft.title)
    for text in texts:
        for mark in _PLACEHOLDER_RE.findall(text):
            if mark not in listed and mark not in found:
                found.append(mark)
    return [*draft.missing_info, *found]


def _run_loop(
    doc_type: DraftType,
    matter_id: str,
    system: str,
    user: str,
    chunks: list[Chunk],
    model: DraftModel,
    judge: SupportJudge | None,
    max_revisions: int,
    stamp: dict,
    run: DraftRun,
) -> DraftDocument:
    """draft -> verify -> revise-with-failures -> re-verify, bounded.

    Judge verdicts are cached content-addressed (claim, page text), so
    re-verifying after a revision re-judges only the paragraphs that changed.
    """
    draft = model.draft(system, user)
    run.attempts += 1
    draft = draft.model_copy(update={"matter_id": matter_id, "doc_type": doc_type, **stamp})

    for round_no in range(max_revisions + 1):
        violations = verify_draft(draft, chunks, judge=judge)
        run.rounds.append(violations)
        if not violations:
            return draft
        if round_no == max_revisions:
            raise DraftDidNotConverge(doc_type, run.attempts, violations)
        draft = model.draft(system, _revision_user(user, draft, violations, chunks))
        run.attempts += 1
        draft = draft.model_copy(update={"matter_id": matter_id, "doc_type": doc_type, **stamp})

    raise AssertionError("unreachable")  # the loop returns or raises


def _finalize(draft: DraftDocument, chunks: list[Chunk]) -> DraftDocument:
    """Stamp code-owned facts on a converged draft: verified flags (verify
    passed, so validate_draft strips nothing) and the placeholder inventory."""
    final, _ = validate_draft(draft, chunks)
    return final.model_copy(update={"missing_info": _collect_missing_info(final)})


def generate_draft(
    matter_id: str,
    doc_type: DraftType,
    chunks: list[Chunk],
    model: DraftModel,
    instructions: str = "",
    *,
    artifacts: MatterArtifacts | None = None,
    judge: SupportJudge | None = None,
    max_revisions: int = MAX_REVISIONS,
    synopsis_component: DraftDocument | None = None,
) -> tuple[DraftDocument, DraftRun]:
    """Generate one court document through the verify-and-revise loop.

    - `artifacts` is REQUIRED for the composed types (synopsis & list of
      dates, SLP): their List of Dates is derived from the verified
      chronology, never re-extracted. For a writ petition it is optional
      context.
    - `synopsis_component` lets the SLP reuse an already-generated Synopsis &
      List of Dates draft instead of generating the component afresh.
    - Raises DraftDidNotConverge when max_revisions is exhausted; the caller
      gets a clean document or a loud failure, nothing in between.
    """
    if doc_type in COMPOSED_TYPES and artifacts is None:
        raise ValueError(
            f"a {doc_type.value} is assembled from the verified chronology — "
            "generate the case brief (artifacts) first"
        )

    run = DraftRun(judge=type(judge).__name__ if judge is not None else None)
    system = f"{DRAFT_SYSTEM}\n\n{GUIDANCE[doc_type]}"
    stamp: dict = {}
    context_extra = ""

    if doc_type in COMPOSED_TYPES:
        assert artifacts is not None
        if doc_type is DraftType.SLP and synopsis_component is not None:
            # The reused component's List of Dates was verified against the
            # record as it was when the component was generated. Re-run the
            # same repair against the chunks as they are NOW — copying it
            # through unchecked would either hide staleness or (worse) stamp
            # unfixable violations into every loop round. (Found by review.)
            lod, run.lod_violations = derive_list_of_dates(
                [
                    ChronologyEvent(
                        event_date=e.event_date,
                        event=e.event,
                        cites=e.cites,
                        confidence=e.confidence,
                    )
                    for e in synopsis_component.list_of_dates
                ],
                chunks,
            )
        else:
            lod, run.lod_violations = derive_list_of_dates(artifacts.chronology, chunks)
        stamp["list_of_dates"] = lod  # code-derived; whatever the model emits is discarded
        context_extra = (
            "\nVERIFIED LIST OF DATES (derived from the record — authoritative; do "
            "NOT produce your own list_of_dates, it is attached in code):\n"
            f"{render_list_of_dates(lod)}\n"
        )

    if doc_type is DraftType.SLP:
        if synopsis_component is None:
            synopsis_component, component_run = generate_draft(
                matter_id,
                DraftType.SYNOPSIS_LOD,
                chunks,
                model,
                instructions,
                artifacts=artifacts,
                judge=judge,
                max_revisions=max_revisions,
            )
            run.component = component_run
        else:
            # The component's prose is stamped onto every loop round, so the
            # model cannot repair it. If it no longer verifies against the
            # current record, fail NOW — before spending a single model call
            # on a loop that is guaranteed not to converge. Free layers only:
            # its claims were support-judged when it was generated.
            stale = verify_draft(
                synopsis_component.model_copy(update={"list_of_dates": stamp["list_of_dates"]}),
                chunks,
            )
            if stale:
                raise ValueError(
                    "The synopsis & list of dates draft being reused no longer "
                    "verifies against the record — documents have changed since "
                    "it was generated. Generate a fresh synopsis & list of dates, "
                    "then draft the SLP again."
                )
        stamp["synopsis"] = synopsis_component.synopsis
        context_extra += (
            "\nVERIFIED SYNOPSIS (already drafted and verified — it will be bound "
            "into this paperbook; keep the petition consistent with it and do not "
            "re-narrate the chronology):\n"
            + "\n".join(p.text for p in synopsis_component.synopsis)
            + "\n"
        )
    elif doc_type is DraftType.WRIT_PETITION and artifacts is not None:
        events = render_list_of_dates(derive_list_of_dates(artifacts.chronology, chunks)[0])
        if events:
            context_extra = (
                "\nVERIFIED CHRONOLOGY (from the case brief — use it to order the "
                "facts, but cite the record for every fact):\n" + events + "\n"
            )

    user = (
        f"Matter ID: {matter_id}\nDocument to draft: {doc_type.value}\n"
        + (f"Drafting instructions from the advocate: {instructions}\n" if instructions else "")
        + context_extra
        + f"\nCASE FILE (every chunk tagged with its source):\n\n{build_context(chunks)}"
    )

    draft = _run_loop(
        doc_type, matter_id, system, user, chunks, model, judge, max_revisions, stamp, run
    )

    if doc_type is DraftType.SYNOPSIS_LOD and not draft.synopsis and draft.paragraphs:
        # The prose is the same either way; a model that wrote it under
        # `paragraphs` is relabelled, not re-asked.
        draft = draft.model_copy(update={"synopsis": draft.paragraphs, "paragraphs": []})

    return _finalize(draft, chunks), run
