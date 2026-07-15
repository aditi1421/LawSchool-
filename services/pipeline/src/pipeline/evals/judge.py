"""Support judges: does the cited page actually support the claim?

Pluggable so the harness works offline (LexicalJudge) and can be upgraded
to an LLM judge without touching the metrics. The judge receives the full
text of the cited page and the claim, and answers supported / unsupported.
"""

from typing import Protocol


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
