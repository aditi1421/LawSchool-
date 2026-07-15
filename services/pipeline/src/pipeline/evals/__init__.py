"""Eval harness — the ship gate.

No artifact-generation change (prompt, model, pipeline) ships unless
`run_gate` passes: citation accuracy >= 0.98, fabrication count == 0,
chronology recall >= 0.90.
"""

from pipeline.evals.gold import GoldEvent, GoldFact, GoldMatter
from pipeline.evals.judge import ClaudeJudge, LexicalJudge, SupportJudge, make_judge
from pipeline.evals.matcher import (
    ClaudeEventMatcher,
    EventMatcher,
    LexicalEventMatcher,
    make_matcher,
)
from pipeline.evals.metrics import EvalReport, score_matter
from pipeline.evals.runner import GATE, run_gate

__all__ = [
    "GATE",
    "ClaudeEventMatcher",
    "ClaudeJudge",
    "EvalReport",
    "EventMatcher",
    "GoldEvent",
    "GoldFact",
    "GoldMatter",
    "LexicalEventMatcher",
    "LexicalJudge",
    "SupportJudge",
    "make_judge",
    "make_matcher",
    "run_gate",
    "score_matter",
]
