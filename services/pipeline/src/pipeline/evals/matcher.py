"""Event matchers: is this generated event the same real-world event as the gold one?

Chronology recall asks whether the agent recovered each hand-annotated event.
The annotator and the agent describe the same event from whatever angle the
record gave them, so the match is semantic, not lexical:

    gold:  "Plaintiffs purchased the suit sites from Rukminibai under two
            registered sale deeds"
    agent: "Rukminibai executed two registered sale deeds (Exs. A1 and A2)
            selling the two suit sites"

One transaction, two perspectives, ~30% token overlap — LexicalEventMatcher
scores it as missed. That is judge error, not agent error, and it is why the
matcher is a seam.

DATES ARE NOT THE MATCHER'S BUSINESS. `metrics._event_matches` compares them
exactly in code and never consults the matcher on a date mismatch: two
different events on the same date must not match, and the same event on
different dates is a real recall miss the harness must keep reporting.
"""

from typing import Protocol

from pydantic import BaseModel

from pipeline.evals.cache import VerdictCache, cache_key, default_cache
from pipeline.evals.judge import _STOPWORDS, _tokens
from pipeline.models import ChronologyEvent


class EventMatcher(Protocol):
    def matches(self, gold_description: str, generated_event: ChronologyEvent) -> bool:
        """True iff generated_event is the same real-world event as gold_description.

        Called only for candidates whose date already matches exactly.
        """
        ...


class LexicalEventMatcher:
    """Offline baseline: token-overlap description match (the original behaviour).

    Default everywhere, so tests and offline runs need no API key. Under-reports
    recall on paraphrase — use ClaudeEventMatcher for the real gate.
    """

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def matches(self, gold_description: str, generated_event: ChronologyEvent) -> bool:
        gold_tokens = _tokens(gold_description) - _STOPWORDS
        gen_tokens = _tokens(generated_event.event) - _STOPWORDS
        if not gold_tokens:
            return False
        return len(gold_tokens & gen_tokens) / len(gold_tokens) >= self.threshold


MATCHER_SYSTEM_PROMPT = """You compare two descriptions of events from a court \
record: one written by a human annotator, one produced by a drafting agent. Both \
are already known to carry the SAME date. You answer one question: do they \
describe the same underlying real-world event?

Answer same_event = true when both describe one event, however differently \
worded:
- Different party's perspective. "The plaintiffs purchased the suit sites from \
Rukminibai under two registered sale deeds" and "Rukminibai executed two \
registered sale deeds selling the two suit sites" are ONE transaction seen from \
the buyer's and the seller's side. Same event.
- Active vs passive voice, and which party is named as the actor.
- Different level of detail. One may add exhibit numbers, a section, an amount, \
or a party's full name; one may compress. Extra or missing detail does not make \
it a different event.
- Different vocabulary for the same act — "executed"/"entered into", \
"filed"/"instituted", "dismissed"/"rejected", "the suit sites"/"the suit \
schedule property".

Answer same_event = false when they are genuinely different events that happen \
to fall on the same date. A record often has several: a suit filed AND an order \
passed, a notice issued AND a reply sent, one deed executed AND another \
registered. Distinct acts, distinct events — even if the parties and the subject \
matter are identical. Also false when the descriptions describe incompatible \
acts (a sale vs a mortgage of the same property), or when they concern different \
parties or different property.

Ask yourself: is there ONE thing that happened here, or TWO? Do not stretch to \
find a match, and do not refuse one over wording."""


class EventMatchVerdict(BaseModel):
    """Structured matcher output — reasoning first so the verdict follows from it."""

    reasoning: str
    same_event: bool


class ClaudeEventMatcher:
    """Claude-backed semantic event matcher — what the real gate runs.

    Verdicts are cached by a hash of (model, gold description, event text); the
    caller pre-filters by date, so only same-date candidates ever reach the API.
    """

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        cache: VerdictCache | None = None,
        client=None,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client
        self._model = model
        self._cache = cache if cache is not None else default_cache("event_match")

    def matches(self, gold_description: str, generated_event: ChronologyEvent) -> bool:
        generated = generated_event.event
        if not gold_description.strip() or not generated.strip():
            return False
        key = cache_key("event_match", self._model, gold_description, generated)
        return self._cache.get_or_compute(key, lambda: self._match(gold_description, generated))

    def _match(self, gold_description: str, generated: str) -> bool:
        user = (
            f"ANNOTATOR'S EVENT:\n<gold>\n{gold_description}\n</gold>\n\n"
            f"AGENT'S EVENT (same date):\n<generated>\n{generated}\n</generated>\n\n"
            "Do these describe the same underlying event?"
        )
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": MATCHER_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=EventMatchVerdict,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(
                f"event matcher returned no parseable output ({response.stop_reason})"
            )
        return parsed.same_event


def make_matcher(kind: str, model: str = "claude-opus-4-8") -> EventMatcher:
    """Build a matcher by name — the CLI seam (`--matcher lexical|claude`)."""
    if kind == "lexical":
        return LexicalEventMatcher()
    if kind == "claude":
        return ClaudeEventMatcher(model=model)
    raise ValueError(f"unknown matcher {kind!r} (expected 'lexical' or 'claude')")
