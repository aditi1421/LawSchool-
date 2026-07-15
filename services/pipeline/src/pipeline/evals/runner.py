"""Gate runner: aggregate per-matter reports and pass/fail the ship gate."""

from dataclasses import dataclass

from pipeline.evals.metrics import EvalReport


@dataclass(frozen=True)
class Gate:
    min_citation_accuracy: float = 0.98
    max_fabrications: int = 0
    min_chronology_recall: float = 0.90


GATE = Gate()


@dataclass
class GateResult:
    passed: bool
    citation_accuracy: float
    fabrication_count: int
    chronology_recall: float
    failures: list[str]
    reports: list[EvalReport]


def run_gate(reports: list[EvalReport], gate: Gate = GATE) -> GateResult:
    if not reports:
        return GateResult(
            passed=False,
            citation_accuracy=0.0,
            fabrication_count=0,
            chronology_recall=0.0,
            failures=["no eval reports — gate cannot pass on an empty gold set"],
            reports=[],
        )

    total_audits = sum(len(r.audits) for r in reports)
    citation_accuracy = (
        sum(sum(a.cited_page_supports for a in r.audits) for r in reports) / total_audits
        if total_audits
        else 1.0
    )
    fabrication_count = sum(r.fabrication_count for r in reports)
    chronology_recall = sum(r.chronology_recall for r in reports) / len(reports)

    failures: list[str] = []
    if citation_accuracy < gate.min_citation_accuracy:
        failures.append(
            f"citation accuracy {citation_accuracy:.3f} < {gate.min_citation_accuracy}"
        )
    if fabrication_count > gate.max_fabrications:
        failures.append(f"fabrication count {fabrication_count} > {gate.max_fabrications}")
    if chronology_recall < gate.min_chronology_recall:
        failures.append(
            f"chronology recall {chronology_recall:.3f} < {gate.min_chronology_recall}"
        )

    return GateResult(
        passed=not failures,
        citation_accuracy=citation_accuracy,
        fabrication_count=fabrication_count,
        chronology_recall=chronology_recall,
        failures=failures,
        reports=reports,
    )
