"""Date extraction for the chronology spine.

Handles the formats that dominate Indian filings:
  12.03.2019 / 12-03-2019 / 12/03/2019   (day first, always)
  12 March 2019 / 12th March, 2019
  March 12, 2019

Extraction only — dates found in text are *mentions*; attaching a mention to
an event is the artifact agent's job, and a date is never inferred where no
mention exists.
"""

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june",
         "july", "august", "september", "october", "november", "december"]
    )
}
_MONTH_RE = "|".join(_MONTHS)

_NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})[,.]?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_FIRST = re.compile(
    rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,.]?\s+(\d{{4}})\b", re.IGNORECASE
)


@dataclass(frozen=True)
class DateMention:
    value: date
    raw: str
    start: int  # character offset in the source text


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_dates(text: str) -> list[DateMention]:
    mentions: dict[int, DateMention] = {}

    for m in _NUMERIC.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        value = _safe_date(year, month, day)  # Indian numeric dates are day-first
        if value:
            mentions[m.start()] = DateMention(value=value, raw=m.group(0), start=m.start())

    for m in _DAY_FIRST.finditer(text):
        value = _safe_date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if value:
            mentions[m.start()] = DateMention(value=value, raw=m.group(0), start=m.start())

    for m in _MONTH_FIRST.finditer(text):
        if m.start() in mentions:
            continue
        value = _safe_date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if value:
            mentions[m.start()] = DateMention(value=value, raw=m.group(0), start=m.start())

    return sorted(mentions.values(), key=lambda d: d.start)
