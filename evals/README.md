# Evals — the ship gate

No artifact-generation change (prompt, model, pipeline) ships unless this harness passes.

## Gold set

10 matters (5 civil, 5 criminal) assembled from public judgments and orders
(Indian Kanoon, eCourts), hand-annotated with:

- gold chronology (every dated event with its source page)
- gold facts (party names, sections invoked, relief claimed, key dates)
- gold document index

Layout:

```
evals/gold/
  files/          # source PDFs — NEVER committed (gitignored)
  annotations/    # gold JSON per matter — committed
```

## Metrics & gate

| Metric | Definition | Gate |
|---|---|---|
| Citation accuracy | cited page actually supports the claim | ≥ 98% |
| Fabrication count | claims with no support anywhere in the record | = 0 |
| Chronology recall | gold events recovered | ≥ 90% |

A single fabricated fact fails the entire run.
