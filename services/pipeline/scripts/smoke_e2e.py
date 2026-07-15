"""Live end-to-end smoke test: synthetic matter -> real Claude artifact agent -> grounded query.

Requires ANTHROPIC_API_KEY (or an active profile). Costs one small Opus call.

    uv run python scripts/smoke_e2e.py
"""

import tempfile
from datetime import date
from pathlib import Path

import fitz

from pipeline.artifacts.generate import AnthropicArtifactModel
from pipeline.ingest.matter import MatterStore
from pipeline.query import AnthropicQueryModel, answer_question
from pipeline.service import matter_chunks, retrieve, run_artifacts

PLAINT = """IN THE COURT OF THE CIVIL JUDGE (SENIOR DIVISION), SAKET, NEW DELHI
Suit No. 482 of 2023

PLAINT
Suit for possession and permanent injunction

1. The plaintiff, Smt. Rekha Sharma, is a resident of C-14, Green Park, New Delhi
and the absolute owner of property bearing No. 42, Khirki Extension, New Delhi
("the suit property").
2. A registered sale deed dated 12.03.2019 was executed between the plaintiff and
defendant no. 1, Shri Mohan Verma, whereby the plaintiff purchased the suit
property for a consideration of Rs. 85,00,000.
3. Despite receipt of the entire sale consideration, defendant no. 1 failed to
hand over vacant physical possession of the suit property to the plaintiff.
4. A legal notice dated 5th June, 2019 was served upon defendant no. 1 calling
upon him to hand over possession within 15 days, which notice went unanswered.
5. The plaintiff accordingly seeks a decree of possession of the suit property
and a permanent injunction restraining the defendants from creating any third
party interest therein.
"""

ORDER = """IN THE COURT OF THE CIVIL JUDGE (SENIOR DIVISION), SAKET, NEW DELHI
Suit No. 482 of 2023
ORDER

Present: Counsel for the plaintiff. Counsel for defendant no. 1.

Written statement on behalf of defendant no. 1 has not been filed despite
opportunity. Last opportunity of two weeks is granted, subject to costs of
Rs. 5,000.

It is further ordered that the parties shall maintain status quo with respect
to title and possession of the suit property until the next date of hearing.

List on 14.08.2023 for filing of written statement and framing of issues.
"""


def make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)
    doc.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        store = MatterStore(tmp_path / "matters")
        manifest = store.create("Sharma v. Verma (smoke)", today=date.today())
        mid = manifest.matter_id

        for name, text in [("plaint.pdf", PLAINT), ("order.pdf", ORDER)]:
            pdf = tmp_path / name
            make_pdf(pdf, text)
            rec = store.add_pdf(mid, name, pdf.read_bytes())
            print(f"ingested {name}: doc_type={rec.doc_type.value}, pages={len(rec.pages)}")

        print(f"chunks: {len(matter_chunks(store, mid))}")
        print("\nrunning artifact agent (live Claude call)...")
        artifacts, violations = run_artifacts(store, mid, AnthropicArtifactModel())

        print(f"\nviolations removed: {len(violations)}")
        for v in violations:
            print(f"  ! {v.artifact}: {v.claim[:60]} -> {v.cite}")

        print(f"\nchronology ({len(artifacts.chronology)} events):")
        for ev in artifacts.chronology:
            when = ev.event_date.isoformat() if ev.event_date else "undated"
            cite = ev.cites[0]
            print(f"  {when}: {ev.event[:70]}  [{cite.file} p.{cite.page}]")

        print(f"\nproceedings ({len(artifacts.proceedings)}):")
        for o in artifacts.proceedings:
            print(f"  {o.order_date}: {o.direction[:70]}")

        print(f"\nissues ({len(artifacts.issues)}):")
        for i in artifacts.issues:
            print(f"  [{i.origin}] {i.text[:70]}")

        print(f"\nnot_found: {artifacts.not_found}")

        print("\ngrounded query (live Claude call)...")
        question = "What was the sale consideration for the suit property?"
        answer = answer_question(question, retrieve(store, mid, question), AnthropicQueryModel())
        print(f"Q: {question}")
        print(f"A: {answer.answer}")
        print(f"cites: {[(c.file, c.page) for c in answer.cites]}")

        question2 = "What is the defendant's monthly income?"
        answer2 = answer_question(question2, retrieve(store, mid, question2), AnthropicQueryModel())
        print(f"\nQ: {question2}")
        print(f"A: {answer2.answer}  (not_found={answer2.not_found})")


if __name__ == "__main__":
    main()
