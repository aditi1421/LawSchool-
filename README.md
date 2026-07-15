# lawschool

AI case-file intelligence for Indian litigators.

Drop a case file — pleadings, orders, evidence, in any mix of scanned, digital, and handwritten English/Hindi documents — and get a hearing-ready brief: chronology of events, chronology of proceedings, rival contentions, issues for determination, and a document index. Every line is clickable back to the exact source page.

**Zero-hallucination by construction:** every factual claim traces to a page in the uploaded record, or is explicitly flagged as not found. Output is gated by an eval harness (citation accuracy, fabrication count, chronology recall) before any user sees it.

## Structure

```
apps/web/           Next.js app — UI, auth, split-view verification viewer
services/pipeline/  Python service — ingestion, OCR, structuring, artifact agents
evals/              Eval harness — gold set, runners, metrics (ship gate)
docs/               Architecture and design docs
```

## Principles

1. **Never fabricate.** Claims trace to the record or say "not found in the record."
2. **Provenance everywhere.** Every extracted chunk carries `{file, page, para}`.
3. **Eval-first.** The faithfulness benchmark is built before the pipeline it measures.
4. **Confidential by default.** Case files are privileged: encrypted at rest, per-user isolation, hard delete, never used for training. Case files are never committed to this repo.

## Development

- Web: `cd apps/web && npm run dev` (http://localhost:3000)
- Pipeline API: `cd services/pipeline && uv run uvicorn pipeline.api:app --port 8010` (http://127.0.0.1:8010)
  Port 8010, not 8000: 8000 is commonly taken, and a second listener on it
  causes intermittent 'cannot reach the backend' errors that look like the
  API is down. 127.0.0.1 rather than localhost pins IPv4 — `localhost`
  resolves to both ::1 and 127.0.0.1 and clients disagree about which to use.
- Database + storage: `docker compose up -d` (Postgres :5433, MinIO :9000)
- Tests: `cd services/pipeline && uv run pytest tests/`
- Live end-to-end smoke (one small Claude call): `uv run python scripts/smoke_e2e.py`
- Ship gate: `uv run python -m pipeline.evals.run` — scores the artifact agent against
  the gold set; fails on citation accuracy < 98%, any fabrication, or recall < 90%.

Copy `services/pipeline/.env.example` to `.env` and set `ANTHROPIC_API_KEY`.
