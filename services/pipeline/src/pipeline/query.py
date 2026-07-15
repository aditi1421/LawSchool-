"""Grounded Q&A over a matter.

Same honesty contract as artifacts: every answer cites pages from the record,
and an unsupported question gets the literal "not found in the record" — the
model is told to refuse, and the schema forces either citations or a not_found
flag so an uncited factual answer is unrepresentable.
"""

from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from pipeline.artifacts.generate import build_context
from pipeline.llm import ClaudeLLM, StructuredLLM, get_llm
from pipeline.models import Chunk, Citation

QUERY_SYSTEM = """You answer questions about an Indian litigation case file using ONLY \
the provided excerpts. Every excerpt is tagged [file | page N | para M].

Rules:
1. Answer only from the excerpts; cite the exact tags supporting each part of the answer.
2. If the excerpts do not contain the answer, set not_found=true and answer exactly \
"not found in the record". Never guess, never use outside knowledge.
3. If excerpts conflict, present both with their citations."""


class GroundedAnswer(BaseModel):
    answer: str
    cites: list[Citation] = Field(default_factory=list)
    not_found: bool = False

    @model_validator(mode="after")
    def cited_or_not_found(self) -> "GroundedAnswer":
        if not self.not_found and not self.cites:
            raise ValueError("an answer must carry citations unless flagged not_found")
        return self


class QueryModel(Protocol):
    def answer(self, system: str, user: str) -> GroundedAnswer: ...


class LLMQueryModel:
    """Grounded Q&A over any StructuredLLM. answer_question still refuses any
    answer whose citations do not resolve, whatever the provider."""

    def __init__(self, llm: StructuredLLM | None = None) -> None:
        self._llm = llm or get_llm()

    def answer(self, system: str, user: str) -> GroundedAnswer:
        return self._llm.generate(system, user, GroundedAnswer)


def AnthropicQueryModel(model: str = "claude-opus-4-8") -> LLMQueryModel:
    return LLMQueryModel(ClaudeLLM(model=model))


def answer_question(
    question: str, retrieved: list[Chunk], model: QueryModel
) -> GroundedAnswer:
    if not retrieved:
        return GroundedAnswer(answer="not found in the record", not_found=True)
    user = f"EXCERPTS:\n\n{build_context(retrieved)}\n\nQUESTION: {question}"
    answer = model.answer(QUERY_SYSTEM, user)

    # Code-enforced: citations must resolve to retrieved pages.
    valid_pages = {(c.location.file, c.location.page) for c in retrieved}
    resolved = [c for c in answer.cites if (c.file, c.page) in valid_pages]
    if not answer.not_found and not resolved:
        return GroundedAnswer(answer="not found in the record", not_found=True)
    return answer.model_copy(update={"cites": resolved})
