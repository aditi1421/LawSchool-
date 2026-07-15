"""LLM provider tests. No network: the HTTP layer is faked."""

import json

import pytest
from pydantic import BaseModel, Field

from pipeline.llm import OllamaLLM, OllamaUnavailable, get_llm
from pipeline.llm.ollama import DEFAULT_TIMEOUT


class Answer(BaseModel):
    verdict: str = Field(pattern="^(yes|no)$")
    reason: str


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_post(monkeypatch, replies: list[str], captured: list | None = None):
    """Queue of /api/chat message contents (raw model text) to return, in order."""
    queue = list(replies)

    def _post(url, json=None, timeout=None):  # noqa: A002
        if captured is not None:
            captured.append(json)
        return FakeResponse({"message": {"content": queue.pop(0)}})

    monkeypatch.setattr("httpx.post", _post)


# -- selection ---------------------------------------------------------------


def test_get_llm_selects_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWSCHOOL_LLM", "ollama")
    monkeypatch.setenv("LAWSCHOOL_LLM_MODEL", "qwen2.5:14b")
    llm = get_llm()
    assert llm.name == "ollama"
    assert llm._model == "qwen2.5:14b"

    monkeypatch.setenv("LAWSCHOOL_LLM", "nonsense")
    with pytest.raises(ValueError, match="unknown LAWSCHOOL_LLM"):
        get_llm()


# -- structured generation ----------------------------------------------------


def test_generate_parses_valid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []
    fake_post(monkeypatch, [json.dumps({"verdict": "yes", "reason": "stated on page 3"})], sent)

    out = OllamaLLM(model="m").generate("sys", "user", Answer)
    assert out.verdict == "yes"

    # The schema is pushed into decoding, not just described in the prompt.
    assert sent[0]["format"]["properties"]["verdict"]["pattern"] == "^(yes|no)$"
    assert sent[0]["options"]["temperature"] == 0.0
    # Ollama defaults to a 2k context and would silently truncate the record.
    assert sent[0]["options"]["num_ctx"] == 32768
    assert sent[0]["messages"][0]["role"] == "system"


def test_schema_violation_is_repaired_with_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constrained decoding still emits invalid output; the retry must show the
    model its own text and the exact violation, not just ask again."""
    sent: list = []
    fake_post(
        monkeypatch,
        [
            json.dumps({"verdict": "maybe", "reason": "unsure"}),  # violates the pattern
            json.dumps({"verdict": "no", "reason": "not in the record"}),
        ],
        sent,
    )

    out = OllamaLLM(model="m").generate("sys", "user", Answer)
    assert out.verdict == "no"
    assert len(sent) == 2

    repair = sent[1]["messages"]
    assert repair[2]["role"] == "assistant" and "maybe" in repair[2]["content"]
    assert "does not satisfy the required schema" in repair[3]["content"]
    assert "verdict" in repair[3]["content"]  # the actual validation error
    # The repair must not invite invention to satisfy required fields.
    assert "Do not invent data" in repair[3]["content"]


def test_unparseable_json_also_repairs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, ["not json at all", json.dumps({"verdict": "yes", "reason": "ok"})])
    assert OllamaLLM(model="m").generate("s", "u", Answer).verdict == "yes"


def test_gives_up_loudly_rather_than_returning_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A weak model that never satisfies the schema must raise — never hand
    back a half-parsed object, since the types carry the provenance rules."""
    fake_post(monkeypatch, [json.dumps({"verdict": "maybe"})] * 3)
    with pytest.raises(RuntimeError, match="did not produce schema-valid output"):
        OllamaLLM(model="m", max_repair_attempts=2).generate("s", "u", Answer)


# -- failure modes -------------------------------------------------------------


def test_server_down_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.post", boom)
    with pytest.raises(OllamaUnavailable, match="ollama serve"):
        OllamaLLM(model="m").generate("s", "u", Answer)


def test_missing_model_names_the_pull_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: FakeResponse({"error": "not found"}, status=404)
    )
    with pytest.raises(OllamaUnavailable, match="ollama pull qwen2.5:14b"):
        OllamaLLM(model="qwen2.5:14b").generate("s", "u", Answer)


def test_available_checks_the_model_is_pulled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: FakeResponse({"models": [{"name": "qwen2.5:14b"}, {"name": "llama3.2:latest"}]}),
    )
    assert OllamaLLM(model="qwen2.5:14b").available()
    assert OllamaLLM(model="llama3.2").available()  # bare name means :latest
    assert not OllamaLLM(model="mistral:7b").available()


def test_timeout_is_generous_enough_for_cpu_inference() -> None:
    # A multi-page judgment on CPU takes minutes; a default HTTP timeout would
    # abort mid-generation and look like a model failure.
    assert DEFAULT_TIMEOUT >= 600
    assert OllamaLLM(model="m")._timeout >= 600


# -- silent truncation guard ---------------------------------------------------


def test_over_long_record_is_refused_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama drops over-long input without saying so. A brief that quietly
    omits half the record is the failure this product exists to prevent."""
    from pipeline.llm.ollama import RecordTooLongForModel

    called = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: called.append(1))

    llm = OllamaLLM(model="m", num_ctx=8192, output_reserve=2000)
    huge = "x" * 200_000  # far beyond the 8k context
    with pytest.raises(RecordTooLongForModel, match="silently truncate"):
        llm.generate("sys", huge, Answer)
    assert not called  # refused before spending minutes of inference


def test_record_within_context_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, [json.dumps({"verdict": "yes", "reason": "ok"})])
    llm = OllamaLLM(model="m", num_ctx=32768, output_reserve=6000)
    assert llm.generate("sys", "a short record", Answer).verdict == "yes"


def test_num_ctx_is_configurable_for_bigger_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_NUM_CTX", "131072")
    assert OllamaLLM(model="m")._num_ctx == 131072


def test_timeout_is_not_reported_as_a_dead_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a 900s ceiling cut qwen2.5:14b off mid-generation on a
    27-page record, and the error said 'is ollama serve running?' about a
    server that was working perfectly."""
    import httpx

    from pipeline.llm.ollama import GenerationTimeout

    def slow(*a, **k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("httpx.post", slow)
    with pytest.raises(GenerationTimeout, match="was not stuck"):
        OllamaLLM(model="qwen2.5:14b", timeout=900).generate("s", "u", Answer)


def test_connection_failure_still_says_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr("httpx.post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(OllamaUnavailable, match="ollama serve"):
        OllamaLLM(model="m").generate("s", "u", Answer)


def test_timeout_ceiling_is_generous_and_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.setenv("OLLAMA_TIMEOUT", "7200")
    import pipeline.llm.ollama as mod

    importlib.reload(mod)
    assert mod.DEFAULT_TIMEOUT == 7200
    monkeypatch.delenv("OLLAMA_TIMEOUT")
    importlib.reload(mod)
    assert mod.DEFAULT_TIMEOUT >= 3600  # 15 minutes was measurably too short
