"""Monetary amount extraction.

Money is the other fact — with dates — that a legal document gets wrong
catastrophically and silently. "Rs. 85,00,000" becoming "Rs. 8,50,000" reads
fine and is a different case.

Indian grouping is lakh/crore, not thousands: 85,00,000 is eighty-five lakh,
which a comma-grouping parser built for 85,000,000 misreads. Values are
normalised to an int so 'Rs. 85,00,000/-', '₹85,00,000' and 'Rs 8500000'
compare equal — the point is the number asserted, not its formatting.

Extraction only. Deciding whether an amount is *supported* belongs to
pipeline.artifacts.fidelity.
"""

import re
from dataclasses import dataclass

# Rs. / Rs / INR / ₹ / Rupees, then the figure, optional paise, optional the
# ubiquitous trailing '/-'.
#
# The digits are matched permissively (\d[\d,]*) rather than by a grouping
# pattern. An earlier version tried to validate grouping with
# `\d{1,3}(?:,\d{2,3})*|\d+` and silently read "₹8500000" as 850: the first
# alternative matched three digits and won, because regex alternation takes the
# first branch that matches, not the longest. Since a currency marker has
# already established this is money, permissive is both safer and simpler —
# commas are stripped anyway.
_AMOUNT = re.compile(
    r"(?:(?:rs|inr|rupees)\.?\s*|₹\s*)"
    r"(\d[\d,]*(?:\.\d{1,2})?)"
    r"\s*(?:/-|/‑)?",
    re.IGNORECASE,
)

# Multipliers that follow the figure: "Rs. 8.5 lakhs", "Rs. 2 crore".
_SCALE = re.compile(r"^\s*(lakh|lakhs|lac|lacs|crore|crores|thousand)\b", re.IGNORECASE)
_SCALES = {
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}


@dataclass(frozen=True)
class AmountMention:
    value: int  # in whole rupees; paise are floored
    raw: str
    start: int


def extract_amounts(text: str) -> list[AmountMention]:
    """Every rupee amount mentioned in the text, normalised to whole rupees."""
    out: list[AmountMention] = []
    for m in _AMOUNT.finditer(text):
        digits = m.group(1).replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue

        raw = m.group(0)
        # A trailing scale word multiplies the figure: "Rs. 8.5 lakhs".
        tail = _SCALE.match(text[m.end() : m.end() + 16])
        if tail:
            value *= _SCALES[tail.group(1).lower()]
            raw = text[m.start() : m.end() + tail.end()]

        out.append(AmountMention(value=int(value), raw=raw.strip(), start=m.start()))
    return out


def amounts_in(text: str) -> set[int]:
    """The set of rupee values the text asserts."""
    return {a.value for a in extract_amounts(text)}


def format_indian(value: int) -> str:
    """Group in the Indian system: 8500000 -> '85,00,000'.

    Shown back to a lawyer, so it must look the way the record does. Python's
    `f"{v:,}"` gives '8,500,000', which is the wrong system and reads as a
    different number to the eye it is written for.
    """
    s = str(abs(value))
    if len(s) <= 3:
        head, tail = "", s
    else:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        head = ",".join(groups) + ","
    return ("-" if value < 0 else "") + head + tail
