"""LLM providers behind the generation seams.

Two providers, one contract (`StructuredLLM`): given a system prompt, a user
prompt and a Pydantic model, return a validated instance of that model.

- Claude — the quality bar. What production runs.
- Ollama — on-machine. Free to iterate against, and the answer for firms who
  will not send privileged case files to a third-party API at all.

Which one is a config choice (LAWSCHOOL_LLM), never a code change: the honesty
rules that make the output safe live in `validate_grounding` / `validate_draft`
and apply to whatever the model returns.
"""

from pipeline.llm.base import StructuredLLM, get_llm
from pipeline.llm.claude import ClaudeLLM
from pipeline.llm.ollama import OllamaLLM, OllamaUnavailable

__all__ = [
    "ClaudeLLM",
    "OllamaLLM",
    "OllamaUnavailable",
    "StructuredLLM",
    "get_llm",
]
