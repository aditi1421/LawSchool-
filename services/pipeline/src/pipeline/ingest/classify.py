"""Doc-type classification.

Heuristic keyword classifier over the first pages' text. Deliberately simple
and transparent; an LLM classifier can replace it behind the same signature.
Order matters: more specific patterns first (a chargesheet mentions the FIR;
an FIR does not mention a chargesheet).
"""

import re

from pipeline.models import DocType

# (doc type, patterns) — first match wins; matched case-insensitively.
_RULES: list[tuple[DocType, list[str]]] = [
    (DocType.CHARGESHEET, [r"charge\s*sheet", r"final\s+report.*173", r"चार्ज\s*शीट", r"आरोप\s*पत्र"]),
    (DocType.FIR, [r"first\s+information\s+report", r"\bF\.?I\.?R\.?\b", r"प्रथम\s+सूचना\s+रिपोर्ट"]),
    (DocType.BAIL_APPLICATION, [r"bail\s+application", r"application.*under\s+section\s+(438|439)", r"जमानत"]),
    (DocType.WRITTEN_STATEMENT, [r"written\s+statement", r"लिखित\s+कथन"]),
    (DocType.REPLICATION, [r"replication", r"rejoinder"]),
    (DocType.PLAINT, [r"\bplaint\b", r"suit\s+for\s+", r"वाद\s*पत्र"]),
    (DocType.JUDGMENT, [r"\bjudgment\b", r"\bjudgement\b", r"निर्णय"]),
    (DocType.ORDER, [r"^\s*order\b", r"\border\s+sheet\b", r"it\s+is\s+ordered", r"आदेश"]),
    (DocType.AFFIDAVIT, [r"affidavit", r"solemnly\s+affirm", r"शपथ\s*पत्र"]),
    (DocType.NOTICE, [r"legal\s+notice", r"notice\s+under\s+section", r"नोटिस"]),
    (DocType.EXHIBIT, [r"\bexhibit\b", r"\bannexure\b", r"प्रदर्श"]),
]


# Standalone heading lines (e.g. a line that IS "ORDER") outrank body-text
# keywords: an order that *mentions* a written statement is still an order.
_HEADINGS: list[tuple[DocType, str]] = [
    (DocType.CHARGESHEET, r"^\s*charge\s*sheet\s*$"),
    (DocType.FIR, r"^\s*first\s+information\s+report\s*$"),
    (DocType.BAIL_APPLICATION, r"^\s*bail\s+application\s*$"),
    (DocType.WRITTEN_STATEMENT, r"^\s*written\s+statement\b.{0,60}$"),
    (DocType.REPLICATION, r"^\s*(replication|rejoinder)\b.{0,40}$"),
    (DocType.PLAINT, r"^\s*plaint\s*$"),
    (DocType.JUDGMENT, r"^\s*judge?ment\s*$"),
    (DocType.ORDER, r"^\s*order(\s+sheet)?\s*$"),
    (DocType.AFFIDAVIT, r"^\s*affidavit\b.{0,60}$"),
    (DocType.NOTICE, r"^\s*legal\s+notice\s*$"),
]


def classify_doc_type(text: str) -> DocType:
    """Classify a document from its first pages' text; OTHER when unsure.

    Heading pass is earliest-match-wins: a document's own title line appears
    before any body-text mention of another document type (an order granting
    time to file a written statement names "ORDER" first).
    """
    haystack = text[:4000]
    heading_hits: list[tuple[int, int, DocType]] = []
    for rank, (doc_type, pattern) in enumerate(_HEADINGS):
        m = re.search(pattern, haystack, re.IGNORECASE | re.MULTILINE)
        if m:
            heading_hits.append((m.start(), rank, doc_type))
    if heading_hits:
        return min(heading_hits)[2]
    for doc_type, patterns in _RULES:
        for pattern in patterns:
            if re.search(pattern, haystack, re.IGNORECASE | re.MULTILINE):
                return doc_type
    return DocType.OTHER
