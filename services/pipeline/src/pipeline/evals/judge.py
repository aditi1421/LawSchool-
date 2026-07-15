"""Support judges: does the cited page actually support the claim?

Pluggable so the harness works offline (LexicalJudge) and can be upgraded
to an LLM judge without touching the metrics. The judge receives the full
text of the cited page and the claim, and answers supported / unsupported.

LexicalJudge is the offline default — token overlap, no API key needed, used
by the tests. ClaudeJudge is what the real gate runs: token overlap scores a
faithful paraphrase as unsupported, which shows up as agent error when it is
judge error.
"""

from typing import Protocol

from pydantic import BaseModel

from pipeline.evals.cache import VerdictCache, cache_key, default_cache


class SupportJudge(Protocol):
    def supports(self, claim: str, page_text: str) -> bool:
        """True iff page_text supports the claim."""
        ...


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in text.lower().split():
        tok = raw.strip(".,;:()[]{}'\"-—")
        if len(tok) > 2:
            out.add(tok)
    return out


_STOPWORDS = {
    "the", "and", "was", "were", "has", "have", "had", "that", "this", "with",
    "for", "from", "are", "not", "but", "his", "her", "its", "their", "они",
    "who", "which", "been", "being", "shall", "may", "any", "all", "such",
}


class LexicalJudge:
    """Offline baseline: token-overlap support check.

    A claim is supported when a sufficient fraction of its content tokens
    appear on the cited page. Deliberately conservative — good enough to
    catch wrong-page citations and outright fabrications in tests; the LLM
    judge replaces it for real gate runs.
    """

    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold

    def supports(self, claim: str, page_text: str) -> bool:
        claim_tokens = _tokens(claim) - _STOPWORDS
        if not claim_tokens:
            return False
        page_tokens = _tokens(page_text)
        overlap = len(claim_tokens & page_tokens) / len(claim_tokens)
        return overlap >= self.threshold


JUDGE_SYSTEM_PROMPT = """You are an adversarial citation auditor for litigation \
briefs. You are given ONE page of text from a court record and ONE factual claim \
that cites that page. You answer a single question: does this page text actually \
establish this claim?

You are STRICT. Your job is to catch a drafting agent that cites confidently and \
inaccurately. A claim that merely sounds plausible, or that a lawyer familiar with \
the case would believe, is NOT supported unless THIS page text states it.

Answer supported = true ONLY when the page text establishes the claim. This \
includes faithful restatement:
- Paraphrase. The claim need not reuse the page's words. "The plaintiffs \
purchased the sites" is supported by a page reading "the vendor sold the sites to \
the plaintiffs" — same fact, different words.
- Perspective and voice. Buyer vs seller, active vs passive, plaintiff's framing \
vs defendant's framing describe the SAME event. "Rukminibai executed two sale \
deeds selling the suit sites" and "the plaintiffs purchased the suit sites from \
Rukminibai under two sale deeds" are the same transaction — supported.
- Summarisation. A claim that compresses several sentences of the page, or omits \
detail the page gives, is supported if nothing it asserts goes beyond the page.
- Terminology. Ordinary legal synonyms and the record's own shorthand \
("the suit sites", "Exs. A1 and A2", "the appellant") count as matches.

Answer supported = false when:
- The page does not state the claim, however plausible it is or however well it \
fits the rest of the case. Absence of the fact from THIS page is decisive; you \
are not judging whether the claim is true, only whether this page establishes it.
- The claim adds a specific the page lacks — a date, an amount, a section \
number, a name, a legal characterisation the page never makes.
- The claim overstates the page. A page recording that a party ALLEGED or \
CONTENDED something does not establish that it happened or was found. A page \
recording an argument does not establish a holding.
- The claim contradicts or subtly alters the page (wrong direction of a \
transaction, wrong party, wrong outcome).
- The page is a different part of the record that happens to share vocabulary \
with the claim. Shared words are not support.

The two failure modes are equally bad: rejecting a faithful paraphrase, and \
accepting a fact the page never states. Reason about what the page actually \
asserts before you answer."""


class SupportVerdict(BaseModel):
    """Structured judge output — reasoning first so the verdict follows from it."""

    page_states: str  # what the page actually establishes, in the judge's words
    reasoning: str  # why the claim does or does not follow from that
    supported: bool


class ClaudeJudge:
    """Claude-backed support judge — the judge the real gate runs on.

    Same `supports(claim, page_text) -> bool` contract as LexicalJudge, so it
    drops into `score_matter` unchanged. Verdicts are cached by a hash of
    (model, claim, page text): identical questions cost one call, ever.
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
        self._cache = cache if cache is not None else default_cache("support")

    def supports(self, claim: str, page_text: str) -> bool:
        if not claim.strip() or not page_text.strip():
            return False
        key = cache_key("support", self._model, claim, page_text)
        return self._cache.get_or_compute(key, lambda: self._judge(claim, page_text))

    def _judge(self, claim: str, page_text: str) -> bool:
        user = (
            f"PAGE TEXT (the full text of the cited page):\n<page>\n{page_text}\n</page>\n\n"
            f"CLAIM (cites this page):\n<claim>\n{claim}\n</claim>\n\n"
            "Does this page text establish this claim?"
        )
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": JUDGE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=SupportVerdict,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(
                f"support judge returned no parseable output ({response.stop_reason})"
            )
        return parsed.supported


def make_judge(kind: str, model: str = "claude-opus-4-8") -> SupportJudge:
    """Build a judge by name — the CLI seam (`--judge lexical|claude`)."""
    if kind == "lexical":
        return LexicalJudge()
    if kind == "claude":
        return ClaudeJudge(model=model)
    raise ValueError(f"unknown judge {kind!r} (expected 'lexical' or 'claude')")
