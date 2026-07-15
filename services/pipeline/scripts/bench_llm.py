"""Compare LLM providers on the artifact task — without spending API credits.

The ship gate needs a Claude judge to score faithfulness of prose. This does
not: every metric here is computed in code against the record itself, so a
local model can be measured for free.

What it measures
  schema_ok        did the provider return a schema-valid object at all
  cite_violations  citations that do not resolve to the record (code-removed)
  date_fidelity    of the dates the model emitted, the fraction that actually
                   occur in the page each was cited to. This is the metric that
                   catches a model reading "12.03.2019" as the year 1203 —
                   citation-valid, page-correct, and factually wrong. The
                   grounding machinery cannot see that; this can.
  date_recall      gold dates the model recovered (exact match)
  coverage         events produced vs gold events
  seconds          wall clock

What it does NOT measure
  Whether the prose faithfully represents the cited text. That needs a judge
  (uv run python -m pipeline.evals.run). A model can score well here and still
  paraphrase dishonestly.

    uv run python scripts/bench_llm.py --provider ollama --model qwen2.5:14b civil-5
"""

import argparse
import json
import sys
import time
from pathlib import Path

from pipeline.artifacts.generate import LLMArtifactModel, generate_artifacts
from pipeline.evals.store import load_gold_matters
from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import extract_pages
from pipeline.models import Chunk
from pipeline.structure import chunk_pages
from pipeline.structure.dates import extract_dates

GOLD_ROOT = Path("../../evals/gold")


def build_llm(provider: str, model: str | None):
    if provider == "ollama":
        from pipeline.llm import OllamaLLM

        llm = OllamaLLM(model=model or OllamaLLM.DEFAULT_MODEL)
        if not llm.available():
            raise SystemExit(f"model {llm._model!r} not pulled — run `ollama pull {llm._model}`")
        return llm
    from pipeline.llm import ClaudeLLM

    return ClaudeLLM(model=model or ClaudeLLM.DEFAULT_MODEL)


def load_matter(gold) -> tuple[list[Chunk], dict[tuple[str, int], str]]:
    chunks: list[Chunk] = []
    pages: dict[tuple[str, int], str] = {}
    for filename in gold.files:
        extracted = extract_pages(GOLD_ROOT / "files" / gold.matter_id / filename)
        head = "\n".join(p.text for p in extracted[:3])
        chunks.extend(chunk_pages(gold.matter_id, filename, classify_doc_type(head), extracted))
        for p in extracted:
            pages[(filename, p.page)] = p.text
    return chunks, pages


def bench(gold, provider: str, model: str | None) -> dict:
    chunks, pages = load_matter(gold)
    llm = build_llm(provider, model)

    started = time.monotonic()
    try:
        artifacts, violations = generate_artifacts(gold.matter_id, chunks, LLMArtifactModel(llm))
    except Exception as exc:
        return {
            "matter": gold.matter_id,
            "schema_ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:150]}",
            "seconds": round(time.monotonic() - started, 1),
        }
    seconds = round(time.monotonic() - started, 1)

    # Date fidelity: every date the model asserts must actually appear in the
    # page it cited. A date the record never states is invented, however
    # plausible — and however correct the citation.
    emitted = 0
    grounded = 0
    invented: list[str] = []
    for ev in artifacts.chronology:
        if ev.event_date is None:
            continue
        emitted += 1
        page_text = pages.get((ev.cites[0].file, ev.cites[0].page), "")
        if ev.event_date in {d.value for d in extract_dates(page_text)}:
            grounded += 1
        else:
            invented.append(f"{ev.event_date} ({ev.event[:44]})")

    gold_dates = {e.event_date for e in gold.events if e.event_date}
    got_dates = {e.event_date for e in artifacts.chronology if e.event_date}
    recalled = len(gold_dates & got_dates)

    return {
        "matter": gold.matter_id,
        "schema_ok": True,
        "events": len(artifacts.chronology),
        "gold_events": len(gold.events),
        "cite_violations": len(violations),
        "dates_emitted": emitted,
        "date_fidelity": round(grounded / emitted, 3) if emitted else None,
        "invented_dates": invented,
        "date_recall": round(recalled / len(gold_dates), 3) if gold_dates else None,
        "seconds": seconds,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ollama", choices=["ollama", "claude"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--json", action="store_true", help="emit raw results")
    ap.add_argument("matters", nargs="*", help="gold matter ids (default: all)")
    args = ap.parse_args()

    wanted = set(args.matters)
    matters = [m for m in load_gold_matters(GOLD_ROOT / "annotations") if not wanted or m.matter_id in wanted]
    if not matters:
        raise SystemExit("no matching gold matters")

    results = []
    for gold in matters:
        print(f"benchmarking {gold.matter_id} on {args.provider}...", flush=True)
        r = bench(gold, args.provider, args.model)
        results.append(r)
        if not r["schema_ok"]:
            print(f"  FAILED: {r['error']}  ({r['seconds']}s)\n", flush=True)
            continue
        print(
            f"  {r['events']}/{r['gold_events']} events | "
            f"cite violations {r['cite_violations']} | "
            f"date fidelity {r['date_fidelity']} | "
            f"date recall {r['date_recall']} | {r['seconds']}s",
            flush=True,
        )
        for bad in r["invented_dates"]:
            print(f"    INVENTED DATE: {bad}", flush=True)
        print(flush=True)

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    ok = [r for r in results if r["schema_ok"]]
    print("=" * 62)
    print(f"provider: {args.provider} ({args.model or 'default'})")
    print(f"schema-valid runs: {len(ok)}/{len(results)}")
    if ok:
        fid = [r["date_fidelity"] for r in ok if r["date_fidelity"] is not None]
        rec = [r["date_recall"] for r in ok if r["date_recall"] is not None]
        print(f"date fidelity (mean): {sum(fid)/len(fid):.3f}" if fid else "date fidelity: n/a")
        print(f"date recall  (mean): {sum(rec)/len(rec):.3f}" if rec else "date recall: n/a")
        print(f"cite violations (total): {sum(r['cite_violations'] for r in ok)}")
        print(f"mean seconds/matter: {sum(r['seconds'] for r in ok)/len(ok):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
