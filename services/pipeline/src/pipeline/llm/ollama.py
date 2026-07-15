"""Ollama provider — on-machine generation.

Why this exists:
- Iterating on prompts and pipeline code costs nothing and needs no key.
- Privileged case files never leave the host. For firms that will not send
  client documents to a third-party API, this is the only acceptable mode.

What it is not: a drop-in for Claude on quality. Local models of the size that
fit on a laptop hallucinate citations far more, and this product's only claim
is that every line traces to the record. That is measurable, not a matter of
opinion — run the ship gate against both and read the numbers.

RAM is the binding constraint, and it is worth knowing before you pull a
model. Measured on an 8GB M2 against a 27-page record (~12.5k tokens):

  qwen2.5:14b   14.5GB resident   never finished; ~2x RAM, thrashed swap
  qwen2.5:7b     7.3GB resident   >27 min, no output; ~11% memory free
  llama3.2:3b    ~2GB             fast, and read "12.03.2019" as the year 1203

Note 7b's 7.3GB is weights *plus* the num_ctx allocation — the context window
is not free, so "the model is 4.7GB" understates what it needs at runtime.
A 7B on an M2 with headroom should do this in 2-4 minutes; the 7-10x overrun
was paging, not computing.

So: 32GB+ for local generation on real case files. Below that, the models that
fit are the ones that get dates wrong, and the honest options are a bigger
machine or the API.

Two things make a small model usable at all here:
- Ollama's `format` takes a JSON Schema and constrains decoding to it, so the
  output parses even when the content is poor. Structural validity is free;
  faithfulness is not.
- The schema-repair retry below. Constrained decoding still emits
  schema-invalid output (wrong enum, missing field), so a failed parse is fed
  back with the validation error rather than thrown away.

Neither makes the model honest. `validate_grounding` / `validate_draft` do
that, in code, on whatever comes back.
"""

import json
import os
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Measured, not guessed: qwen2.5:14b was still generating a 27-page record at
# 15 minutes when the old 900s ceiling cut it off and the failure was
# misreported as "Ollama is down". A large local model on a long record is
# genuinely this slow — the ceiling exists to stop a wedged request, not to
# pace the model.
DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "3600"))


class OllamaUnavailable(RuntimeError):
    """Ollama is not reachable, or the requested model is not pulled."""


class GenerationTimeout(RuntimeError):
    """The model was still generating when we stopped waiting.

    Distinct from OllamaUnavailable on purpose: conflating them reports a
    working server as down and sends the reader to debug the wrong thing.
    """


class RecordTooLongForModel(RuntimeError):
    """The record does not fit the model's context window.

    Ollama truncates over-long input silently. That would hand back a brief
    covering only part of the record with no indication anything was dropped —
    the exact dishonesty this product exists to prevent. Refusing is the only
    safe behaviour.
    """


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token). Deliberately crude and slightly
    pessimistic: this guards a silent-truncation cliff, so over-estimating
    costs a false refusal while under-estimating costs a silently wrong brief.
    """
    return len(text) // 3


class OllamaLLM:
    # Deliberately not one of the tiny models: a 1.5-3B model cannot hold a
    # multi-page judgment and emit exact citations. This is the smallest size
    # worth measuring; expect to need larger for real quality.
    DEFAULT_MODEL = "qwen2.5:14b"
    name = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        timeout: float | None = None,
        num_ctx: int | None = None,
        max_repair_attempts: int = 2,
        output_reserve: int = 6000,
    ) -> None:
        self._model = model
        self._host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT
        # Ollama defaults to a small context (2k) regardless of the model's
        # capability, silently truncating the record — which would look like
        # the model ignoring documents. Set it explicitly.
        self._num_ctx = num_ctx or int(os.environ.get("OLLAMA_NUM_CTX", "32768"))
        self._max_repair_attempts = max_repair_attempts
        self._output_reserve = output_reserve

    # -- plumbing -----------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        import httpx

        try:
            resp = httpx.post(f"{self._host}{path}", json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            # NOT the same as "Ollama is down", and reporting it that way sends
            # the reader to check a server that is working fine. The model was
            # generating; we stopped waiting.
            raise GenerationTimeout(
                f"{self._model} was still generating after "
                f"{self._timeout / 60:.0f} minutes and was cut off. It was not "
                f"stuck — a large model on a long record is genuinely this slow. "
                f"Raise OLLAMA_TIMEOUT (seconds), use a smaller model, or use "
                f"Claude for this matter."
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(
                f"cannot reach Ollama at {self._host} — is `ollama serve` running? ({exc})"
            ) from exc
        if resp.status_code == 404:
            raise OllamaUnavailable(
                f"model {self._model!r} is not available — run `ollama pull {self._model}`"
            )
        resp.raise_for_status()
        return resp.json()

    def available(self) -> bool:
        import httpx

        try:
            resp = httpx.get(f"{self._host}/api/tags", timeout=5.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            return False
        names = {m.get("name", "") for m in resp.json().get("models", [])}
        # `ollama list` shows "qwen2.5:14b"; a bare "qwen2.5" means ":latest".
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return wanted in names

    def _chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        data = self._post(
            "/api/chat",
            {
                "model": self._model,
                "messages": messages,
                "stream": False,
                # JSON Schema here constrains decoding — the model cannot emit
                # tokens that break the shape.
                "format": schema,
                "options": {
                    "num_ctx": self._num_ctx,
                    # Legal extraction is not a creative task: take the mode.
                    "temperature": 0.0,
                },
            },
        )
        return data.get("message", {}).get("content", "")

    # -- the seam -----------------------------------------------------------
    def generate(self, system: str, user: str, schema: type[T]) -> T:
        json_schema = schema.model_json_schema()

        # Refuse rather than let Ollama silently drop the tail of the record.
        # Reserve room for the model's own output.
        prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
        budget = self._num_ctx - self._output_reserve
        if prompt_tokens > budget:
            raise RecordTooLongForModel(
                f"This record is about {prompt_tokens:,} tokens, which does not fit "
                f"{self._model}'s {self._num_ctx:,}-token context (leaving "
                f"{self._output_reserve:,} for the brief). Ollama would silently "
                f"truncate it and produce a brief that quietly omits part of the "
                f"record. Use a model with a larger context, raise num_ctx if the "
                f"model supports it, or use Claude for this matter."
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_error: Exception | None = None
        for attempt in range(self._max_repair_attempts + 1):
            raw = self._chat(messages, json_schema)
            try:
                return schema.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == self._max_repair_attempts:
                    break
                # Show the model its own output and the exact violation. A
                # bare "try again" tends to reproduce the same mistake.
                messages = messages[:2] + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "That output does not satisfy the required schema.\n\n"
                            f"Validation error:\n{exc}\n\n"
                            "Return corrected JSON matching the schema exactly. "
                            "Do not invent data to fill required fields — if the "
                            "record does not support a value, omit the item."
                        ),
                    },
                ]

        raise RuntimeError(
            f"{schema.__name__}: {self._model} did not produce schema-valid output after "
            f"{self._max_repair_attempts + 1} attempts. Last error: {last_error}"
        )
