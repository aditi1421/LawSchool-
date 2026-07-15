"""The grounded artifact agent.

Flow: chunks -> tagged context -> Claude (structured output, schema = MatterArtifacts)
-> validate_grounding (code-enforced honesty) -> clean artifacts + violations.

The model is asked to cite; the code verifies every citation against the actual
chunk set. A claim whose citations don't all resolve is REMOVED from the
artifacts and reported as a GroundingViolation — it never reaches the user.
Claims supported only by low-confidence OCR pages are kept but downgraded.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pipeline.artifacts.prompts import MATTER_GUIDANCE, SYSTEM_PROMPT
from pipeline.llm import ClaudeLLM, StructuredLLM, get_llm
from pipeline.models import Chunk, Citation, MatterArtifacts

OCR_CONFIDENCE_THRESHOLD = 0.7


class ArtifactModel(Protocol):
    """The generation seam — a fake stands in for tests."""

    def generate(self, system: str, user: str) -> MatterArtifacts: ...


class LLMArtifactModel:
    """Artifact generation over any StructuredLLM (Claude or Ollama).

    The provider is a config choice; the honesty guarantee is not. Whatever
    comes back goes through validate_grounding before anyone sees it.
    """

    def __init__(self, llm: StructuredLLM | None = None) -> None:
        self._llm = llm or get_llm()

    def generate(self, system: str, user: str) -> MatterArtifacts:
        return self._llm.generate(system, user, MatterArtifacts)


def AnthropicArtifactModel(model: str = "claude-opus-4-8") -> LLMArtifactModel:
    """Claude-backed artifact model. Kept as a named entry point for callers
    that must pin Claude regardless of LAWSCHOOL_LLM (the eval gate)."""
    return LLMArtifactModel(ClaudeLLM(model=model))


def build_context(chunks: list[Chunk]) -> str:
    """Render chunks with their provenance tags — the only text the model sees."""
    lines: list[str] = []
    for chunk in chunks:
        loc = chunk.location
        para = f" | para {loc.para}" if loc.para is not None else ""
        low = " | LOW-CONFIDENCE OCR" if chunk.ocr_confidence < OCR_CONFIDENCE_THRESHOLD else ""
        lines.append(f"[{loc.file} | page {loc.page}{para}{low}] ({chunk.doc_type.value})\n{chunk.text}")
    return "\n\n".join(lines)


@dataclass
class GroundingViolation:
    kind: Literal["unresolvable_citation"]
    artifact: str  # which artifact list the claim came from
    claim: str
    cite: Citation


def _index_chunks(chunks: list[Chunk]) -> dict[tuple[str, int], list[Chunk]]:
    pages: dict[tuple[str, int], list[Chunk]] = {}
    for chunk in chunks:
        pages.setdefault((chunk.location.file, chunk.location.page), []).append(chunk)
    return pages


def validate_grounding(
    artifacts: MatterArtifacts, chunks: list[Chunk]
) -> tuple[MatterArtifacts, list[GroundingViolation]]:
    """Code-enforced honesty rules.

    - A citation must resolve to a real (file, page) in the chunk set; a claim
      with any unresolvable citation is removed and reported.
    - A chronology event supported ONLY by low-confidence-OCR pages is kept but
      downgraded to confidence="low_ocr" (never silently trusted).
    """
    pages = _index_chunks(chunks)
    violations: list[GroundingViolation] = []

    def cites_resolve(artifact: str, claim: str, cites: list[Citation]) -> bool:
        ok = True
        for cite in cites:
            if (cite.file, cite.page) not in pages:
                violations.append(
                    GroundingViolation(
                        kind="unresolvable_citation", artifact=artifact, claim=claim, cite=cite
                    )
                )
                ok = False
        return ok

    def only_low_ocr(cites: list[Citation]) -> bool:
        supports = [
            c
            for cite in cites
            for c in pages.get((cite.file, cite.page), [])
        ]
        return bool(supports) and all(
            c.ocr_confidence < OCR_CONFIDENCE_THRESHOLD for c in supports
        )

    chronology = []
    for ev in artifacts.chronology:
        if not cites_resolve("chronology", ev.event, ev.cites):
            continue
        if only_low_ocr(ev.cites):
            ev = ev.model_copy(update={"confidence": "low_ocr"})
        chronology.append(ev)

    proceedings = [
        o for o in artifacts.proceedings if cites_resolve("proceedings", o.direction, o.cites)
    ]

    contentions = []
    for cont in artifacts.contentions:
        pet = cont.petitioner
        res = cont.respondent
        if pet and not cites_resolve("contentions", pet.position, pet.cites):
            pet = None
        if res and not cites_resolve("contentions", res.position, res.cites):
            res = None
        if pet or res:
            contentions.append(cont.model_copy(update={"petitioner": pet, "respondent": res}))

    issues = [i for i in artifacts.issues if cites_resolve("issues", i.text, i.cites) or not i.cites]

    return (
        artifacts.model_copy(
            update={
                "chronology": chronology,
                "proceedings": proceedings,
                "contentions": contentions,
                "issues": issues,
            }
        ),
        violations,
    )


def generate_artifacts(
    matter_id: str,
    chunks: list[Chunk],
    model: ArtifactModel,
) -> tuple[MatterArtifacts, list[GroundingViolation]]:
    """Run the agent over a matter's chunks and enforce grounding."""
    system = f"{SYSTEM_PROMPT}\n\n{MATTER_GUIDANCE}"
    user = (
        f"Matter ID: {matter_id}\n\n"
        f"CASE FILE (every chunk tagged with its source):\n\n{build_context(chunks)}"
    )
    raw = model.generate(system, user)
    raw = raw.model_copy(update={"matter_id": matter_id})
    return validate_grounding(raw, chunks)
