"""Eval harness — the ship gate.

No artifact-generation change (prompt, model, pipeline) ships unless
`run_gate` passes: citation accuracy >= 0.98, fabrication count == 0,
chronology recall >= 0.90.
"""

from pipeline.evals.gold import GoldEvent, GoldFact, GoldMatter
from pipeline.evals.judge import LexicalJudge, SupportJudge
from pipeline.evals.metrics import EvalReport, score_matter
from pipeline.evals.runner import GATE, run_gate

__all__ = [
    "GATE",
    "EvalReport",
    "GoldEvent",
    "GoldFact",
    "GoldMatter",
    "LexicalJudge",
    "SupportJudge",
    "run_gate",
    "score_matter",
]
