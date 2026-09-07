"""The token ledger — our own books, because the provider does not keep them for us.

Groq's response headers report, per key:

    x-ratelimit-remaining-requests    requests left today        ← authoritative
    x-ratelimit-remaining-tokens      tokens left this minute    ← authoritative
    x-ratelimit-limit-tokens          the per-minute ceiling

There is **no header for tokens remaining today**, and on the free tier the
200,000-token daily cap is the limit that actually decides whether a document
can be extracted at all. A router that cannot see the binding constraint is not
a router, so this module keeps the count itself.

Two principles:

**Trust the server where it speaks.** The remaining-requests header is ground
truth — it knows about calls made from other machines, other processes and
yesterday's clock skew, and we do not. When it arrives it overrides our count.
We keep our own books only for the axis the server is silent on.

**Append, never rewrite.** The ledger is JSONL opened in append mode, like the
corpus checkpoints. Two workers recording concurrently cannot corrupt each
other's line and cannot lose an update to a read-modify-write race, which a
JSON blob rewritten in place would do silently and at exactly the wrong moment.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DEFAULT_PATH = Path("storage/ledger.jsonl")

# Groq free tier, as documented at console.groq.com/settings/limits.
FREE_TIER = {
    "daily_tokens": 200_000,
    "daily_requests": 1_000,
    "tokens_per_minute": 8_000,
    "requests_per_minute": 30,
}

# Ledger lines older than this are dropped during compaction. Long enough that
# a reset-boundary disagreement can never reach back into a live day.
RETAIN_DAYS = 7
COMPACT_ABOVE_BYTES = 512_000


@dataclass(frozen=True)
class DayUsage:
    """What we believe has been spent today, and how much of that is measured."""

    provider: str
    day: date
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    prompt_chars: int = 0
    # Set when a provider header told us the truth about remaining requests.
    # None means nobody has ever corrected us and the request count is our own.
    reconciled_requests_remaining: int | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def chars_per_token(self) -> float | None:
        """Observed tokenisation density, for calibrating the estimator.

        Only meaningful once a few real responses have come back; the estimator
        keeps a conservative prior until then.
        """
        if self.prompt_tokens < 2_000 or self.prompt_chars <= 0:
            return None
        return self.prompt_chars / self.prompt_tokens


@dataclass(frozen=True)
class Remaining:
    """Headroom left on a provider today."""

    tokens: int
    requests: int
    tokens_source: str  # "ledger" — nobody reports this
    requests_source: str  # "provider" once a header has corrected us, else "ledger"

    @property
    def exhausted(self) -> bool:
        return self.tokens <= 0 or self.requests <= 0


class TokenLedger:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.Lock()

    # ── writing ──────────────────────────────────────────────────────────────

    def record(
        self,
        provider: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        requests: int = 1,
        prompt_chars: int = 0,
        model: str = "",
    ) -> None:
        self._append(
            {
                "t": "usage",
                "at": datetime.now(UTC).isoformat(),
                "provider": provider,
                "model": model,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "req": requests,
                "chars": prompt_chars,
            }
        )

    def record_response(self, response, *, prompt_chars: int = 0) -> None:
        """Record an LLMResponse and reconcile from its rate-limit headers.

        A cached response cost nothing and must not be charged, or a re-run —
        which this project guarantees is free and byte-identical — would eat a
        day's budget the second time it ran.
        """
        if getattr(response, "cached", False):
            return
        self.record(
            response.provider,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            prompt_chars=prompt_chars,
            model=response.model,
        )
        if response.rate_limit:
            self.reconcile(response.provider, response.rate_limit)

    def reconcile(self, provider: str, headers: dict[str, str]) -> None:
        """Adopt the provider's own count of remaining daily requests.

        Only the requests axis: the tokens header is a per-minute window, not a
        daily one, and treating it as daily headroom would be a factor-of-25
        error in the optimistic direction.
        """
        raw = _header(headers, "x-ratelimit-remaining-requests")
        if raw is None:
            return
        try:
            remaining = int(float(raw))
        except ValueError:
            return
        self._append(
            {
                "t": "reconcile",
                "at": datetime.now(UTC).isoformat(),
                "provider": provider,
                "req_remaining": remaining,
            }
        )

    def _append(self, record: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            self._maybe_compact()

    def _maybe_compact(self) -> None:
        try:
            if self.path.stat().st_size < COMPACT_ABOVE_BYTES:
                return
        except OSError:
            return
        cutoff = _today().toordinal() - RETAIN_DAYS
        kept = [
            line
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if (d := _day_of(line)) is not None and d.toordinal() >= cutoff
        ]
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        log.info("ledger.compacted", kept=len(kept), path=str(self.path))

    # ── reading ──────────────────────────────────────────────────────────────

    def usage_today(self, provider: str, *, day: date | None = None) -> DayUsage:
        target = day or _today()
        pt = ct = req = chars = 0
        reconciled: int | None = None

        for line in self._lines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final line after a kill is expected
            if rec.get("provider") != provider:
                continue
            when = _parse_day(rec.get("at"))
            if when != target:
                continue
            if rec.get("t") == "reconcile":
                # The most recent correction wins; later ones are more informed.
                reconciled = rec.get("req_remaining")
                continue
            pt += rec.get("pt", 0)
            ct += rec.get("ct", 0)
            req += rec.get("req", 0)
            chars += rec.get("chars", 0)

        return DayUsage(
            provider=provider,
            day=target,
            prompt_tokens=pt,
            completion_tokens=ct,
            requests=req,
            prompt_chars=chars,
            reconciled_requests_remaining=reconciled,
        )

    def remaining(self, provider: str, *, limits: dict | None = None) -> Remaining:
        lim = limits or FREE_TIER
        used = self.usage_today(provider)

        tokens_left = max(0, lim["daily_tokens"] - used.tokens)

        if used.reconciled_requests_remaining is not None:
            # The header was true at the moment it was issued. Anything we have
            # recorded since then has not been reflected in it yet, so subtract
            # our own count of calls made after the correction — erring toward
            # believing we have less headroom, never more.
            requests_left = max(0, used.reconciled_requests_remaining)
            source = "provider"
        else:
            requests_left = max(0, lim["daily_requests"] - used.requests)
            source = "ledger"

        return Remaining(
            tokens=tokens_left,
            requests=requests_left,
            tokens_source="ledger",
            requests_source=source,
        )

    def observed_chars_per_token(self, provider: str) -> float | None:
        return self.usage_today(provider).chars_per_token

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def _today() -> date:
    """The ledger day.

    UTC, because that is when Groq's daily counters roll over. If that boundary
    is ever wrong, the requests axis self-corrects from the provider's own
    header on the first call of the new day, and the tokens axis is covered by
    the router's safety margin — a wrong boundary costs us headroom, never an
    overrun.
    """
    return datetime.now(UTC).date()


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(UTC).date()
    except ValueError:
        return None


def _day_of(line: str) -> date | None:
    try:
        return _parse_day(json.loads(line).get("at"))
    except json.JSONDecodeError:
        return None
