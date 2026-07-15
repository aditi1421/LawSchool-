"""The structured-generation contract, and provider selection."""

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    """Generate a validated instance of `schema`.

    Implementations must return a parsed, schema-valid object or raise —
    never a half-parsed dict. Callers rely on the type to hold provenance
    invariants (a citation-less artifact row is unrepresentable), so a
    provider that returned loose JSON would defeat the entire design.
    """

    name: str

    def generate(self, system: str, user: str, schema: type[T]) -> T: ...


def get_llm() -> StructuredLLM:
    """Resolve the configured provider.

    LAWSCHOOL_LLM=claude (default) | ollama
    LAWSCHOOL_LLM_MODEL overrides the model id for either.
    """
    provider = os.environ.get("LAWSCHOOL_LLM", "claude").lower()
    model = os.environ.get("LAWSCHOOL_LLM_MODEL") or None

    if provider == "ollama":
        from pipeline.llm.ollama import OllamaLLM

        return OllamaLLM(model=model or OllamaLLM.DEFAULT_MODEL)
    if provider == "claude":
        from pipeline.llm.claude import ClaudeLLM

        return ClaudeLLM(model=model or ClaudeLLM.DEFAULT_MODEL)
    raise ValueError(f"unknown LAWSCHOOL_LLM={provider!r} (expected 'claude' or 'ollama')")
