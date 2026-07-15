"""Claude provider — the quality bar."""

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ClaudeLLM:
    DEFAULT_MODEL = "claude-opus-4-8"
    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            # The system prompt is identical across every call for a given
            # task, so cache it rather than re-paying for it per document.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(
                f"{schema.__name__}: no parseable output (stop_reason={response.stop_reason})"
            )
        return parsed
