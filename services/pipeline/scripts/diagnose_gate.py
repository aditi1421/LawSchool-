"""Diagnose a failed ship-gate run: show what actually failed, per matter.

Prints the claims scored as fabrications and the gold events that were missed,
so the failure can be attributed to the agent vs. the judge.

    uv run python scripts/diagnose_gate.py [--judge lexical|claude]
                                           [--matcher lexical|claude] [matter_id ...]

Defaults match the real gate (claude/claude). Run with --judge lexical to see
what the old token-overlap judge made of the same output.
"""

import argparse
from pathlib import Path

from pipeline.artifacts.generate import AnthropicArtifactModel, generate_artifacts
from pipeline.evals.judge import make_judge
from pipeline.evals.matcher import make_matcher
from pipeline.evals.metrics import PageTexts, score_matter
from pipeline.evals.store import load_gold_matters
from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import extract_pages
from pipeline.models import Chunk
from pipeline.structure import chunk_pages

GOLD_ROOT = Path("../../evals/gold")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["lexical", "claude"], default="claude")
    parser.add_argument("--matcher", choices=["lexical", "claude"], default="claude")
    parser.add_argument("matter_ids", nargs="*")
    args = parser.parse_args()

    wanted = set(args.matter_ids)
    matters = [m for m in load_gold_matters(GOLD_ROOT / "annotations") if not wanted or m.matter_id in wanted]
    model = AnthropicArtifactModel()
    judge = make_judge(args.judge)
    matcher = make_matcher(args.matcher)

    for gold in matters:
        files_dir = GOLD_ROOT / "files" / gold.matter_id
        chunks: list[Chunk] = []
        pages: PageTexts = {}
        for filename in gold.files:
            extracted = extract_pages(files_dir / filename)
            head = "\n".join(p.text for p in extracted[:3])
            chunks.extend(chunk_pages(gold.matter_id, filename, classify_doc_type(head), extracted))
            for p in extracted:
                pages[(filename, p.page)] = p.text

        artifacts, violations = generate_artifacts(gold.matter_id, chunks, model)
        report = score_matter(gold, artifacts, pages, judge, matcher)

        print(f"\n{'=' * 70}\n{gold.matter_id}  ({len(gold.events)} gold events)")
        print(f"  judge/matcher: {args.judge}/{args.matcher}")
        print(f"  citation acc {report.citation_accuracy:.2f} | fabrications "
              f"{report.fabrication_count} | recall {report.chronology_recall:.2f}")
        print(f"  agent produced {len(artifacts.chronology)} chronology events")
        if violations:
            print(f"  code-removed violations: {len(violations)}")

        fabs = [a for a in report.audits if not a.supported_anywhere]
        if fabs:
            print(f"\n  -- scored as FABRICATION ({len(fabs)}) --")
            for a in fabs[:5]:
                print(f"     claim: {a.claim[:100]}")
                print(f"     cited: {a.cite.file} p.{a.cite.page}")
                page = pages.get((a.cite.file, a.cite.page), "")
                print(f"     cited page text: {page[:160].strip()!r}\n")

        if report.missed_gold_events:
            print(f"  -- MISSED gold events ({len(report.missed_gold_events)}) --")
            for g in report.missed_gold_events[:5]:
                print(f"     {g.event_date}: {g.description[:80]}")
                near = [
                    e for e in artifacts.chronology if e.event_date == g.event_date
                ]
                if near:
                    print(f"       agent had same date: {near[0].event[:80]}")
                else:
                    print("       agent has no event on that date")


if __name__ == "__main__":
    main()
