"""Ship-gate runner: score the artifact agent against the gold set.

Usage:
    uv run python -m pipeline.evals.run [--gold-root ../../evals/gold] [--model claude-opus-4-8]
                                        [--judge lexical|claude] [--matcher lexical|claude]

For each gold matter with source files present under <gold-root>/files/<matter_id>/,
this ingests the PDFs, runs the real artifact agent, scores against the gold
annotations, and pass/fails the gate. Matters whose files are absent are skipped
with a warning (source PDFs are never committed).

The real gate uses the Claude judge and matcher (the default). The lexical pair
scores on token overlap: no API key, no cost, and no credit for paraphrase — an
offline smoke-test of the harness, not a gate result to trust.

Exit code 0 = gate passed, 1 = gate failed or nothing to run.
"""

import argparse
import sys
from pathlib import Path

from pipeline.artifacts.generate import AnthropicArtifactModel
from pipeline.evals.gold import GoldMatter
from pipeline.evals.judge import SupportJudge, make_judge
from pipeline.evals.matcher import EventMatcher, make_matcher
from pipeline.evals.metrics import EvalReport, PageTexts, score_matter
from pipeline.evals.runner import run_gate
from pipeline.evals.store import load_gold_matters
from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import extract_pages
from pipeline.models import Chunk
from pipeline.structure import chunk_pages


def evaluate_matter(
    gold: GoldMatter,
    files_dir: Path,
    model,
    judge: SupportJudge,
    matcher: EventMatcher,
) -> EvalReport:
    chunks: list[Chunk] = []
    pages: PageTexts = {}
    for filename in gold.files:
        extracted = extract_pages(files_dir / filename)
        head = "\n".join(p.text for p in extracted[:3])
        chunks.extend(chunk_pages(gold.matter_id, filename, classify_doc_type(head), extracted))
        for p in extracted:
            pages[(filename, p.page)] = p.text

    from pipeline.artifacts.generate import generate_artifacts

    artifacts, violations = generate_artifacts(gold.matter_id, chunks, model)
    if violations:
        print(f"  ! {len(violations)} grounding violation(s) removed pre-scoring")
    return score_matter(gold, artifacts, pages, judge, matcher)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-root", default="../../evals/gold")
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument(
        "--judge",
        choices=["lexical", "claude"],
        default="claude",
        help="citation support judge (default: claude; lexical is offline only)",
    )
    parser.add_argument(
        "--matcher",
        choices=["lexical", "claude"],
        default="claude",
        help="chronology event matcher (default: claude; lexical is offline only)",
    )
    args = parser.parse_args()

    gold_root = Path(args.gold_root)
    matters = load_gold_matters(gold_root / "annotations")
    if not matters:
        print("no gold annotations found — gate cannot run")
        return 1

    model = AnthropicArtifactModel(model=args.model)
    judge = make_judge(args.judge, args.model)
    matcher = make_matcher(args.matcher, args.model)
    reports: list[EvalReport] = []
    for gold in matters:
        files_dir = gold_root / "files" / gold.matter_id
        if not all((files_dir / f).exists() for f in gold.files):
            print(f"skip {gold.matter_id}: source files missing under {files_dir}")
            continue
        print(f"evaluating {gold.matter_id} ({gold.lens})...")
        reports.append(evaluate_matter(gold, files_dir, model, judge, matcher))

    result = run_gate(reports)
    print("\n=== SHIP GATE ===")
    print(f"matters scored:      {len(reports)}")
    print(f"judge / matcher:     {args.judge} / {args.matcher}")
    print(f"citation accuracy:   {result.citation_accuracy:.3f}  (gate ≥ 0.98)")
    print(f"fabrication count:   {result.fabrication_count}  (gate = 0)")
    print(f"chronology recall:   {result.chronology_recall:.3f}  (gate ≥ 0.90)")
    print(f"RESULT: {'PASS' if result.passed else 'FAIL'}")
    for failure in result.failures:
        print(f"  - {failure}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
