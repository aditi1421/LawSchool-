# Architecture

## System overview

```
┌─────────────────────────────┐      ┌──────────────────────────────────┐
│  apps/web (Next.js)         │      │  services/pipeline (Python)      │
│                             │      │                                  │
│  upload UI                  │─────▶│  ingest:   OCR + provenance      │
│  matter dashboard           │      │            doc-type classifier   │
│  split-view verifier        │◀─────│  structure: para chunking,       │
│  (artifact ⇄ source page)   │      │            embeddings, dates     │
│  query UI                   │      │  artifacts: grounded agent,      │
│  .docx export               │      │            civil/criminal lenses │
└─────────────┬───────────────┘      └───────────────┬──────────────────┘
              │                                      │
              ▼                                      ▼
        Postgres (+ pgvector)  ◀──────────  object storage (uploads)
              ▲
              │  ship gate: no artifact version deploys without passing
        evals/ harness (gold set · citation accuracy · fabrication · recall)
```

## Components

- **apps/web** — Next.js (TS, Tailwind). Auth, matter/file management, the split-view
  verification viewer (the trust feature: click any artifact row → jump to and
  highlight the cited page), query UI, `.docx` export.
- **services/pipeline** — Python (FastAPI). Three stages:
  - `ingest`: text-layer extraction (PyMuPDF) with OCR fallback (EasyOCR local,
    cloud OCR as fallback) for English + Hindi; per-page confidence; doc-type
    classification.
  - `structure`: chunking by legal paragraph numbering; embeddings into pgvector;
    date extraction for the chronology spine.
  - `artifacts`: long-horizon grounded agent (Claude) emitting typed artifacts
    through lens templates (civil, criminal). Honesty rules enforced in code,
    not just prompts.
- **evals** — gold set of annotated matters + runner. Metrics: citation accuracy
  (≥98%), fabrication count (must be 0), chronology recall (≥90%). Runs on every
  prompt/model change; failing the gate blocks release.

## Data model (provenance chain)

```
Matter ─▶ Document ─▶ Page ─▶ Chunk {file, page, para, text, lang, ocr_confidence}
                                 │
Artifacts (chronology, orders, contentions, issues, doc index)
  — every row REQUIRES ≥1 citation to a Chunk, or an explicit `not_found` flag.
```

The citation requirement is encoded in the Pydantic types: an artifact row without
provenance is unrepresentable.

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Vector store | Postgres + pgvector (managed, e.g. Neon/Supabase) | One database for everything; managed; India region available |
| OCR | PyMuPDF text layer → EasyOCR → cloud fallback | Most Indian filings are scans; escalate only when needed |
| Agent model | Claude API | Long-context grounded extraction; citations enforced via tool schemas |
| Eval-first | Harness built before the pipeline | Trust is the product; unmeasured faithfulness is a liability |
| Grounding scope (v1) | User's own uploaded documents only | Maximum value, zero legal-DB licensing exposure |

## Honesty rules (enforced in code)

1. Page below OCR-confidence threshold → flagged, never the sole support for a claim.
2. Undated events → explicit "undated" bucket; dates are never inferred.
3. Documents conflict on a fact → conflict surfaced with both citations.
4. Unsupported query → literal "not found in the record."

## Drafting: verify-and-revise

Drafting (`pipeline/drafting/`) runs a bounded loop, not a single shot:
draft → verify → revise-with-failures → re-verify → produce. Verification is
three layers (`drafting/verify.py`), each answering a different question:

| Layer | asks | cost |
|---|---|---|
| resolution | does every citation point at a real page? | free |
| fidelity | do the asserted dates/amounts occur on the cited page? | free |
| support | does the page genuinely establish the claim? | LLM call |

Resolution and fidelity are pure code (shared with `artifacts/fidelity.py`)
and protect every provider equally. Support judging is **risk-gated**: only
paragraphs asserting a date, a rupee amount, or a party's obligation are
judged (`assertion_risks` — a testable predicate, not a vibe), by the same
`ClaudeJudge` the eval gate uses, verdict-cached on disk. A draft that cannot
converge within the revision budget raises `DraftDidNotConverge` — it never
silently ships its best guess.

Paperbook composition: the List of Dates is **derived in code** from the
already-verified chronology (`derive_list_of_dates`), never re-extracted, and
the SLP consumes the Synopsis and List of Dates as components — a re-derived
chronology could contradict the standalone one.
