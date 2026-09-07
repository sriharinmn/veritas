"""Numeric parsing.

Everything downstream compares `canonical_magnitude`, so this module is the
foundation the comparator stands on. It is pure, has no dependencies on the rest
of the system, and is exercised by a large table of cases — which is exactly
what you want for the layer that decides whether two numbers are the same.

The cases that actually appear in Indian filings and institutional reports:

    1,23,456.78     Indian digit grouping (lakhs/crores)
    123,456.78      international grouping
    (2,345)         negative in parentheses — standard in financial statements
    2,345-          trailing-minus, seen in older tabular exports
    NIL, —, –       nil markers, which are a value, not a missing value
    8.2%            percent
    35 bps          basis points
    1.4x            a multiple
    6.3 to 6.8      a range, which corroborates by containment
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from core.models import RatioBasis, TypedValue, ValueKind
from core.normalize.currency import detect_currency
from core.normalize.scale import parse_scale

# A nil marker is a *stated* value, not an absent one. "Contingent liabilities:
# NIL" is a fact worth extracting, and treating it as missing loses it.
NIL_TOKENS = {
    "nil", "n.i.l", "none", "—", "–", "-", "‐", "0", "0.0",
    "na", "n.a.", "n/a", "not applicable", "not available",
}

# Comma groups of two or three digits, which covers Indian (1,23,456) and
# international (123,456) grouping in one pattern. Deliberately permissive: the
# comma is a separator in both conventions, so stripping it yields the right
# value either way, and being strict about grouping style here would only
# reject real numbers written slightly unusually.
_NUM = r"\d+(?:,\d{2,3})*(?:\.\d+)?|\.\d+"

NUMBER_RE = re.compile(_NUM)

# Ranges: "6.3 to 6.8", "6.3-6.8", "between 6.3 and 6.8", "6.3 – 6.8 per cent"
_RANGE_RE = re.compile(
    rf"(?:between\s+)?({_NUM})\s*(?:to|and|–|—|-|~)\s*({_NUM})",
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(r"%|\bper\s*cent(?:age)?\b|\bpercent\b", re.IGNORECASE)
_BPS_RE = re.compile(r"\bbps\b|\bbasis\s+points?\b", re.IGNORECASE)
_MULTIPLE_RE = re.compile(r"(?<=\d)\s*x\b", re.IGNORECASE)

_PAREN_NEG_RE = re.compile(r"\(\s*([^()]*\d[^()]*)\s*\)")
_TRAIL_NEG_RE = re.compile(r"(\d)\s*-\s*$")


class ParsedNumber:
    __slots__ = ("value", "negative", "raw")

    def __init__(self, value: Decimal, negative: bool, raw: str) -> None:
        self.value = value
        self.negative = negative
        self.raw = raw

    @property
    def signed(self) -> Decimal:
        return -self.value if self.negative else self.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ParsedNumber({self.signed})"


def parse_number(text: str) -> ParsedNumber | None:
    """Parse the first number in `text`, honouring financial sign conventions.

    Comma stripping is safe for both Indian and international grouping because
    the comma is a group separator in each. The grouping *style* matters for
    deciding whether a token is a number at all, not for its value.
    """
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None

    if s.strip().lower() in NIL_TOKENS:
        return ParsedNumber(Decimal(0), False, s)

    negative = False

    m = _PAREN_NEG_RE.search(s)
    if m:
        negative = True
        s = m.group(1)
    elif _TRAIL_NEG_RE.search(s):
        negative = True
    elif re.search(r"(?<![\w.])-\s*\d", s):
        negative = True

    m = NUMBER_RE.search(s.replace(" ", ""))
    if not m:
        return None
    try:
        value = Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None

    return ParsedNumber(value, negative, text.strip())


def parse_range(text: str) -> tuple[Decimal, Decimal] | None:
    """Parse "6.3 to 6.8" and friends.

    Ranges matter because a range and a point estimate can *agree*: an IMF
    projection of 6.5% sitting inside an Economic Survey range of 6.3-6.8% is a
    corroboration, not a contradiction, and a system that cannot represent the
    range will report the wrong thing.
    """
    m = _RANGE_RE.search(text or "")
    if not m:
        return None
    try:
        lo = Decimal(m.group(1).replace(",", ""))
        hi = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation:
        return None
    if lo > hi:
        lo, hi = hi, lo
    # A hyphen between two numbers is ambiguous — it is as likely to be a date
    # range or a page range as a numeric one. Require the two ends to be within
    # an order of magnitude before believing it.
    if hi != 0 and lo != 0 and (hi / lo) > 10:
        return None
    return lo, hi


def classify_ratio(text: str) -> RatioBasis | None:
    if _BPS_RE.search(text or ""):
        return RatioBasis.BPS
    if _PERCENT_RE.search(text or ""):
        return RatioBasis.PERCENT
    if _MULTIPLE_RE.search(text or ""):
        return RatioBasis.MULTIPLE
    return None


def to_canonical_ratio(magnitude: Decimal, basis: RatioBasis) -> Decimal:
    """Canonicalise every ratio to a plain fraction.

    8.2%, 820 bps and 0.082 are one value written three ways, and the comparator
    should never have to know which form it was given.
    """
    match basis:
        case RatioBasis.PERCENT:
            return magnitude / Decimal(100)
        case RatioBasis.BPS:
            return magnitude / Decimal(10_000)
        case RatioBasis.MULTIPLE | RatioBasis.RAW:
            return magnitude


def parse_value(text: str, context: str = "") -> TypedValue | None:
    """Parse a numeric value with its unit, scale and currency.

    `context` is the surrounding text — a table header path, a caption, the
    document-level "(₹ in millions)" statement. Scale and currency are looked
    for in the value first and the context second, which is what lets a bare
    "72,251" in a table cell inherit its meaning from a header fifty pages back.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return None

    combined = f"{raw} {context}"

    basis = classify_ratio(raw) or classify_ratio(context)
    currency = detect_currency(raw) or detect_currency(context)
    scale = parse_scale(raw) or parse_scale(context) or Decimal(1)

    rng = parse_range(raw)
    parsed = parse_number(raw)
    if parsed is None and rng is None:
        return None

    if basis is not None:
        magnitude = to_canonical_ratio(parsed.signed, basis) if parsed else None
        lo = hi = None
        if rng:
            lo = to_canonical_ratio(rng[0], basis)
            hi = to_canonical_ratio(rng[1], basis)
            if magnitude is None or not (lo <= magnitude <= hi):
                magnitude = (lo + hi) / 2
        return TypedValue(
            kind=ValueKind.RATIO,
            raw=raw,
            canonical_magnitude=magnitude,
            ratio_basis=basis,
            range_low=lo,
            range_high=hi,
        )

    if parsed is None:
        return None

    magnitude = parsed.signed * scale
    lo = rng[0] * scale if rng else None
    hi = rng[1] * scale if rng else None

    if currency:
        return TypedValue(
            kind=ValueKind.MONEY,
            raw=raw,
            canonical_magnitude=magnitude,
            currency=currency,
            range_low=lo,
            range_high=hi,
        )

    return TypedValue(
        kind=ValueKind.QUANTITY,
        raw=raw,
        canonical_magnitude=magnitude,
        unit=_unit_hint(combined),
        range_low=lo,
        range_high=hi,
    )


_UNIT_HINTS = (
    ("tonne", "t"), ("tonnes", "t"), ("kg", "kg"), ("km", "km"),
    ("sq ft", "sqft"), ("square feet", "sqft"), ("sq. ft", "sqft"),
    ("employees", "count"), ("shipments", "count"), ("parcels", "count"),
    ("customers", "count"), ("shares", "count"), ("people", "count"),
)


def _unit_hint(text: str) -> str | None:
    low = (text or "").lower()
    for needle, unit in _UNIT_HINTS:
        if needle in low:
            return unit
    return None
