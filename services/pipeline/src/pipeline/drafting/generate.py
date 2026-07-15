"""The drafting agent: record -> grounded court document.

Same seam pattern as artifacts: a DraftModel protocol with an Anthropic
implementation, and code-enforced verification after generation —
citations must resolve to the record; factual paragraphs must be either
cited or carry an explicit placeholder.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pipeline.artifacts.generate import build_context
from pipeline.drafting.models import DraftDocument, DraftType
from pipeline.drafting.prompts import DRAFT_SYSTEM, GUIDANCE
from pipeline.llm import ClaudeLLM, StructuredLLM, get_llm
from pipeline.models import Chunk, Citation

PLACEHOLDER_MARK = "[●"


class DraftModel(Protocol):
    def draft(self, system: str, user: str) -> DraftDocument: ...


class LLMDraftModel:
    """Drafting over any StructuredLLM. validate_draft enforces the honesty
    rules on the result regardless of provider."""

    def __init__(self, llm: StructuredLLM | None = None) -> None:
        self._llm = llm or get_llm()

    def draft(self, system: str, user: str) -> DraftDocument:
        return self._llm.generate(system, user, DraftDocument)


def AnthropicDraftModel(model: str = "claude-opus-4-8") -> LLMDraftModel:
    return LLMDraftModel(ClaudeLLM(model=model))


@dataclass
class DraftViolation:
    kind: Literal["unresolvable_citation", "uncited_factual_paragraph"]
    paragraph: str
    cite: Citation | None = None


def validate_draft(
    draft: DraftDocument, chunks: list[Chunk]
) -> tuple[DraftDocument, list[DraftViolation]]:
    """Code-enforced honesty for drafts.

    - Citations that don't resolve to the record are stripped and reported;
      the paragraph text stays (drafts are work product) but is left unverified.
    - A factual paragraph with no surviving citation and no [●] placeholder is
      reported: it asserts matter facts with no traceable source.
    - `verified` is recomputed here; whatever the model set is discarded.
    """
    pages = {(c.location.file, c.location.page) for c in chunks}
    violations: list[DraftViolation] = []
    paragraphs = []

    for para in draft.paragraphs:
        resolved = []
        for cite in para.cites:
            if (cite.file, cite.page) in pages:
                resolved.append(cite)
            else:
                violations.append(
                    DraftViolation(
                        kind="unresolvable_citation", paragraph=para.text[:120], cite=cite
                    )
                )
        if para.kind == "boilerplate":
            verified = len(resolved) == len(para.cites)
        else:
            has_placeholder = PLACEHOLDER_MARK in para.text
            if not resolved and not has_placeholder:
                violations.append(
                    DraftViolation(kind="uncited_factual_paragraph", paragraph=para.text[:120])
                )
                verified = False
            else:
                verified = len(resolved) == len(para.cites) and (bool(resolved) or has_placeholder)
        paragraphs.append(para.model_copy(update={"cites": resolved, "verified": verified}))

    return draft.model_copy(update={"paragraphs": paragraphs}), violations


def generate_draft(
    matter_id: str,
    doc_type: DraftType,
    chunks: list[Chunk],
    model: DraftModel,
    instructions: str = "",
) -> tuple[DraftDocument, list[DraftViolation]]:
    system = f"{DRAFT_SYSTEM}\n\n{GUIDANCE[doc_type]}"
    user = (
        f"Matter ID: {matter_id}\nDocument to draft: {doc_type.value}\n"
        + (f"Drafting instructions from the advocate: {instructions}\n" if instructions else "")
        + f"\nCASE FILE (every chunk tagged with its source):\n\n{build_context(chunks)}"
    )
    raw = model.draft(system, user)
    raw = raw.model_copy(update={"matter_id": matter_id, "doc_type": doc_type})
    return validate_draft(raw, chunks)
