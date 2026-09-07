"""Currency detection.

Deliberately conservative: an unrecognised symbol returns None rather than a
guess. A wrong currency turns a corroboration into a contradiction, and a
contradiction is the output a reader is most likely to act on — so the cost of
guessing here is higher than the cost of admitting ignorance.
"""

from __future__ import annotations

import re

SYMBOLS: dict[str, str] = {
    "₹": "INR",
    "रु": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}

WORDS: dict[str, str] = {
    "inr": "INR",
    "rs": "INR",
    "rs.": "INR",
    "rupees": "INR",
    "rupee": "INR",
    "usd": "USD",
    "us$": "USD",
    "dollars": "USD",
    "dollar": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "sterling": "GBP",
    "jpy": "JPY",
    "yen": "JPY",
}

_WORD_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in sorted(WORDS, key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def detect_currency(text: str) -> str | None:
    """Return an ISO-4217 code, or None when the text names no currency."""
    if not text:
        return None
    for sym, code in SYMBOLS.items():
        if sym in text:
            return code
    m = _WORD_RE.search(text)
    if m:
        return WORDS[m.group(1).lower()]
    return None
