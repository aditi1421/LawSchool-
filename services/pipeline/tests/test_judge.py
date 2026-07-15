"""Judge/matcher seam tests.

The eval harness scores an LLM's output with another LLM. These tests prove the
seams — not the model: every judgement here comes from a fake, so the suite runs
offline and deterministically. What's under test is that score_matter honours a
pluggable judge and matcher, that dates stay a code decision, and that we don't
pay for the same judgement twice.
"""

import threading
import time
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from pipeline.evals import (
    GoldEvent,
    GoldMatter,
    LexicalEventMatcher,
    LexicalJudge,
    make_judge,
    make_matcher,
    score_matter,
)
from pipeline.evals.cache import VerdictCache, cache_key
from pipeline.evals.judge import ClaudeJudge, SupportVerdict
from pipeline.evals.matcher import ClaudeEventMatcher, EventMatchVerdict
from pipeline.models import Citation, ChronologyEvent, MatterArtifacts

# One sale, described from each side — the annotator writes it from the buyer's
# perspective, the agent from the seller's, and they share almost no vocabulary.
GOLD_DESCRIPTION = (
    "Plaintiffs purchased the suit sites from Rukminibai under two registered sale deeds"
)
AGENT_DESCRIPTION = (
    "Rukminibai conveyed both schedule properties to the plaintiffs by way of Exs. A1 and A2"
)
SALE_DATE = date(2019, 3, 12)

PAGES = {
    ("plaint.pdf", 3): (
        "Rukminibai executed two registered sale deeds marked Exs. A1 and A2 "
        "conveying the two suit schedule properties to the plaintiffs on 12 March 2019."
    ),
}

GOLD = GoldMatter(
    matter_id="paraphrase-1",
    files=["plaint.pdf"],
    events=[
        GoldEvent(
            event_date=SALE_DATE,
            description=GOLD_DESCRIPTION,
            source=Citation(file="plaint.pdf", page=3),
        )
    ],
)


def agent_chronology(text: str, when: date | None = SALE_DATE) -> MatterArtifacts:
    return MatterArtifacts(
        matter_id="paraphrase-1",
        chronology=[
            ChronologyEvent(
                event_date=when, event=text, cites=[Citation(file="plaint.pdf", page=3)]
            )
        ],
    )


# --- fakes -------------------------------------------------------------------


@dataclass
class StubMatcher:
    """Semantic matcher stand-in: verdict is fixed, calls are counted."""

    verdict: bool
    calls: int = 0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def matches(self, gold_description: str, generated_event: ChronologyEvent) -> bool:
        with self._lock:
            self.calls += 1
        return self.verdict


@dataclass
class StubJudge:
    verdict: bool
    calls: int = 0

    def supports(self, claim: str, page_text: str) -> bool:
        self.calls += 1
        return self.verdict


class FakeAnthropic:
    """Minimal `client.messages.parse` stand-in that counts calls."""

    def __init__(self, verdict_factory, delay: float = 0.0) -> None:
        self._verdict_factory = verdict_factory
        self._delay = delay
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
        if self._delay:
            time.sleep(self._delay)  # widen the window for a concurrent duplicate
        return SimpleNamespace(parsed_output=self._verdict_factory(kwargs), stop_reason="end_turn")

    @property
    def call_count(self) -> int:
        return len(self.calls)


def support_verdict(supported: bool):
    return lambda _kwargs: SupportVerdict(
        page_states="...", reasoning="...", supported=supported
    )


def match_verdict(same: bool):
    return lambda _kwargs: EventMatchVerdict(reasoning="...", same_event=same)


def memory_cache() -> VerdictCache:
    return VerdictCache(path=None)


# --- the bug the seam exists to fix -----------------------------------------


def test_lexical_matcher_misses_the_same_event_in_the_other_party_voice() -> None:
    """Documents the false failure: one sale, two voices, scored as missed."""
    report = score_matter(
        GOLD, agent_chronology(AGENT_DESCRIPTION), PAGES, LexicalJudge(), LexicalEventMatcher()
    )
    assert report.chronology_recall == 0.0
    assert report.missed_gold_events == GOLD.events


def test_semantic_matcher_recovers_the_paraphrased_event() -> None:
    """Same output, same gold, semantic matcher: recall goes 0.0 -> 1.0."""
    matcher = StubMatcher(verdict=True)
    report = score_matter(
        GOLD, agent_chronology(AGENT_DESCRIPTION), PAGES, LexicalJudge(), matcher
    )
    assert report.chronology_recall == 1.0
    assert report.missed_gold_events == []
    assert matcher.calls == 1


def test_matcher_defaults_to_lexical_for_existing_callers() -> None:
    """score_matter without a matcher keeps the old behaviour exactly."""
    report = score_matter(GOLD, agent_chronology(AGENT_DESCRIPTION), PAGES, LexicalJudge())
    assert report.chronology_recall == 0.0


# --- dates are code's decision, not the LLM's --------------------------------


def test_date_mismatch_never_matches_however_eager_the_matcher() -> None:
    matcher = StubMatcher(verdict=True)  # would match anything it is asked about
    report = score_matter(
        GOLD,
        agent_chronology(GOLD_DESCRIPTION, when=date(2019, 3, 13)),  # off by one day
        PAGES,
        LexicalJudge(),
        matcher,
    )
    assert report.chronology_recall == 0.0
    assert matcher.calls == 0  # pre-filtered in code; never reached the matcher


def test_undated_gold_event_does_not_match_a_dated_one() -> None:
    undated_gold = GOLD.model_copy(
        update={"events": [GOLD.events[0].model_copy(update={"event_date": None})]}
    )
    matcher = StubMatcher(verdict=True)
    report = score_matter(
        undated_gold, agent_chronology(GOLD_DESCRIPTION), PAGES, LexicalJudge(), matcher
    )
    assert report.chronology_recall == 0.0
    assert matcher.calls == 0


def test_different_events_on_the_same_date_are_not_a_match() -> None:
    """Same date is a candidate, not a match — the matcher still gets a veto."""
    matcher = StubMatcher(verdict=False)
    report = score_matter(
        GOLD,
        agent_chronology("Suit filed in the court of the Civil Judge"),
        PAGES,
        LexicalJudge(),
        matcher,
    )
    assert report.chronology_recall == 0.0
    assert matcher.calls == 1  # date matched, so it was asked; it said no


# --- the judge seam ----------------------------------------------------------


def test_judge_seam_drives_citation_accuracy() -> None:
    strict = StubJudge(verdict=False)
    report = score_matter(GOLD, agent_chronology(AGENT_DESCRIPTION), PAGES, strict)
    assert report.citation_accuracy == 0.0
    assert report.fabrication_count == 1

    lenient = StubJudge(verdict=True)
    report = score_matter(GOLD, agent_chronology(AGENT_DESCRIPTION), PAGES, lenient)
    assert report.citation_accuracy == 1.0
    assert report.fabrication_count == 0


def test_claude_judge_returns_the_models_verdict() -> None:
    client = FakeAnthropic(support_verdict(True))
    judge = ClaudeJudge(client=client, cache=memory_cache())
    assert judge.supports("Plaintiffs purchased the suit sites", PAGES[("plaint.pdf", 3)])

    client = FakeAnthropic(support_verdict(False))
    judge = ClaudeJudge(client=client, cache=memory_cache())
    assert not judge.supports("Defendant admitted liability", PAGES[("plaint.pdf", 3)])


def test_claude_judge_sends_claim_page_and_schema() -> None:
    client = FakeAnthropic(support_verdict(True))
    ClaudeJudge(model="claude-opus-4-8", client=client, cache=memory_cache()).supports(
        "the claim", "the page text"
    )
    (kwargs,) = client.calls
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is SupportVerdict
    assert kwargs["thinking"] == {"type": "adaptive"}
    user = kwargs["messages"][0]["content"]
    assert "the claim" in user and "the page text" in user


def test_claude_judge_skips_the_api_on_an_empty_page() -> None:
    client = FakeAnthropic(support_verdict(True))
    judge = ClaudeJudge(client=client, cache=memory_cache())
    assert not judge.supports("a claim", "   ")
    assert client.call_count == 0


def test_claude_judge_raises_when_output_is_unparseable() -> None:
    client = FakeAnthropic(lambda _kwargs: None)
    judge = ClaudeJudge(client=client, cache=memory_cache())
    with pytest.raises(RuntimeError, match="no parseable output"):
        judge.supports("a claim", "a page")


# --- caching -----------------------------------------------------------------


def test_repeated_question_costs_one_call() -> None:
    client = FakeAnthropic(support_verdict(True))
    judge = ClaudeJudge(client=client, cache=memory_cache())
    for _ in range(5):
        assert judge.supports("same claim", "same page")
    assert client.call_count == 1


def test_different_questions_are_not_conflated() -> None:
    client = FakeAnthropic(support_verdict(True))
    judge = ClaudeJudge(client=client, cache=memory_cache())
    judge.supports("claim A", "page one")
    judge.supports("claim B", "page one")
    judge.supports("claim A", "page two")
    assert client.call_count == 3


def test_concurrent_duplicate_claims_make_one_call() -> None:
    """The thread pool must not turn one question into eight."""
    client = FakeAnthropic(support_verdict(True), delay=0.05)
    judge = ClaudeJudge(client=client, cache=memory_cache())
    duplicated = MatterArtifacts(
        matter_id="paraphrase-1",
        chronology=[
            ChronologyEvent(
                event_date=SALE_DATE,
                event=AGENT_DESCRIPTION,
                cites=[Citation(file="plaint.pdf", page=3)],
            )
        ]
        * 8,
    )
    report = score_matter(GOLD, duplicated, PAGES, judge, StubMatcher(verdict=True))
    assert len(report.audits) == 8
    assert all(a.cited_page_supports for a in report.audits)
    assert client.call_count == 1


def test_matcher_caches_verdicts() -> None:
    client = FakeAnthropic(match_verdict(True))
    matcher = ClaudeEventMatcher(client=client, cache=memory_cache())
    event = ChronologyEvent(
        event_date=SALE_DATE, event=AGENT_DESCRIPTION, cites=[Citation(file="plaint.pdf", page=3)]
    )
    assert matcher.matches(GOLD_DESCRIPTION, event)
    assert matcher.matches(GOLD_DESCRIPTION, event)
    assert client.call_count == 1


def test_cache_survives_the_process(tmp_path) -> None:
    path = tmp_path / "support.json"
    first = FakeAnthropic(support_verdict(True))
    ClaudeJudge(client=first, cache=VerdictCache(path)).supports("a claim", "a page")
    assert first.call_count == 1

    second = FakeAnthropic(support_verdict(False))  # would flip the verdict if called
    assert ClaudeJudge(client=second, cache=VerdictCache(path)).supports("a claim", "a page")
    assert second.call_count == 0


def test_corrupt_cache_file_is_a_miss_not_a_crash(tmp_path) -> None:
    path = tmp_path / "support.json"
    path.write_text("{not json")
    client = FakeAnthropic(support_verdict(True))
    assert ClaudeJudge(client=client, cache=VerdictCache(path)).supports("a claim", "a page")
    assert client.call_count == 1


def test_cache_key_separates_adjacent_fields() -> None:
    assert cache_key("ab", "c") != cache_key("a", "bc")


# --- the CLI seam ------------------------------------------------------------


def test_make_judge_and_matcher_build_the_offline_pair() -> None:
    assert isinstance(make_judge("lexical"), LexicalJudge)
    assert isinstance(make_matcher("lexical"), LexicalEventMatcher)


def test_unknown_judge_or_matcher_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown judge"):
        make_judge("gpt")
    with pytest.raises(ValueError, match="unknown matcher"):
        make_matcher("regex")
