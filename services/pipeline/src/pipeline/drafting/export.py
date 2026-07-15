"""Export a draft to court-format .docx."""

from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from pipeline.drafting.models import DraftDocument
from pipeline.models import Citation


def _cite_note(cites: list[Citation]) -> str:
    return "; ".join(
        f"{c.file} p.{c.page}" + (f" ¶{c.para}" if c.para is not None else "") for c in cites
    )


def draft_to_docx(draft: DraftDocument, include_citation_notes: bool = True) -> bytes:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)

    if draft.court_header:
        for line in draft.court_header.splitlines():
            p = doc.add_paragraph(line)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(draft.title)
    run.bold = True
    run.underline = True

    n = 0
    for para in draft.paragraphs:
        if para.kind == "factual":
            n += 1
            p = doc.add_paragraph(f"{n}. {para.text}")
        else:
            p = doc.add_paragraph(para.text)
        p.paragraph_format.space_after = Pt(8)
        if include_citation_notes and para.cites:
            note = doc.add_paragraph(f"[record: {_cite_note(para.cites)}]")
            note.runs[0].font.size = Pt(8)
            note.runs[0].italic = True

    if draft.prayer:
        heading = doc.add_paragraph()
        heading.add_run("PRAYER").bold = True
        for i, relief in enumerate(draft.prayer, 1):
            doc.add_paragraph(f"({chr(96 + i)}) {relief}")

    if draft.missing_info:
        doc.add_page_break()
        warn = doc.add_paragraph()
        warn.add_run("DRAFTING NOTES — information not on the record (verify and fill):").bold = True
        for item in draft.missing_info:
            doc.add_paragraph(item, style="List Bullet")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
