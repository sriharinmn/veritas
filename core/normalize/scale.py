"""Magnitude scales.

Indian financial documents mix Indian and international scales freely, often in
the same table: a figure stated in crore in the narrative and in millions in the
statement, or "(₹ in lakhs)" in a header that never repeats. Getting this wrong
does not produce a small error — it produces a contradiction that is off by a
factor of ten, a hundred, or ten million, and it will look like a real finding.
"""

from __future__ import annotations

import re
from decimal import Decimal

# Ordered longest-first so "thousand crore" beats "crore" during matching.
SCALES: dict[str, Decimal] = {
    "thousand crore": Decimal(10) ** 10,
    "lakh crore": Decimal(10) ** 12,
    "thousand": Decimal(10) ** 3,
    "lakhs": Decimal(10) ** 5,
    "lakh": Decimal(10) ** 5,
    "lacs": Decimal(10) ** 5,
    "lac": Decimal(10) ** 5,
    "crores": Decimal(10) ** 7,
    "crore": Decimal(10) ** 7,
    "cr": Decimal(10) ** 7,
    "millions": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "mln": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "trillion": Decimal(10) ** 12,
    "tn": Decimal(10) ** 12,
    "units": Decimal(1),
    "absolute": Decimal(1),
}

_SCALE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(SCALES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# "in ₹ million", "(Rs. in crores)", "amounts in lakhs", "figures in ' 000"
_HEADER_RE = re.compile(
    r"(?:\(|\b)(?:all\s+)?(?:amounts?|figures?|numbers?|values?|₹|rs\.?|inr|usd|\$)?\s*"
    r"(?:in|are\s+in)\s+(?:₹\s*|rs\.?\s*|inr\s*|usd\s*|\$\s*)?"
    r"(thousand|lakhs?|lacs?|crores?|millions?|billions?|mn|bn|cr|'?\s?000)",
    re.IGNORECASE,
)


def parse_scale(text: str) -> Decimal | None:
    """Return the multiplier named in `text`, or None if it names no scale."""
    m = _SCALE_RE.search(text or "")
    if not m:
        return None
    return SCALES[m.group(1).lower()]


def scale_from_header(text: str) -> Decimal | None:
    """Extract a scale from a table header or document-level statement.

    Handles the ubiquitous "(₹ in millions)" caption, including the typographic
    "' 000" form. This is the mechanism behind document-context inheritance:
    financial PDFs state the scale once and then never again, so a claim
    extracted fifty pages later has to inherit it.
    """
    m = _HEADER_RE.search(text or "")
    if not m:
        return None
    token = m.group(1).lower().replace(" ", "").replace("'", "")
    if token == "000":
        return Decimal(10) ** 3
    return SCALES.get(token) or SCALES.get(token.rstrip("s"))
