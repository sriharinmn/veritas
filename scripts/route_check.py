"""Show the routing decision for a document, and optionally test it for real.

Two modes, both useful to somebody evaluating this repository:

    python -m scripts.route_check <pdf>            # decide only, costs nothing
    python -m scripts.route_check <pdf> --spend 2  # actually extract 2 pages

The first prints the router's whole reasoning — the estimate, every tier's
assessment, which of Groq's four rate limits binds, and why the winner won. It
makes no network call to any model and spends nothing.

The second is the honesty check on the first. It extracts a couple of pages on
the chosen tier and compares the predicted token cost against the billed one,
because an estimator that has never been checked against a real invoice is a
guess with a dataclass around it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.gateway import build_gateway
from core.extract.llm import extract_page, read_document_context
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.parse.pdf import parse_pdf
from core.route import TokenLedger, route_document
from core.route.estimate import estimate_document
from core.settings import Tier


async def main(path: str, spend_pages: int) -> int:
    src = Path(path)
    if not src.exists():
        print(f"no such file: {src}")
        return 1

    doc = parse_pdf(src)
    spots = spot_document(doc)
    ledger = TokenLedger()

    print(f"\n{src.name} — {doc.page_count} pages, {spots.total:,} candidates spotted\n")

    before = ledger.usage_today("groq")
    # When we are about to spend, route the *same* slice we will extract.
    # Routing the whole document and then extracting two pages of it would be
    # comparing an estimate against an invoice for different work.
    budget = spend_pages if spend_pages > 0 else None
    decision = route_document(spots, ledger=ledger, page_budget=budget)

    print("routing")
    print("-" * 72)
    for line in decision.trace:
        print(f"  {line}")
    print()
    print("tier assessments")
    print("-" * 72)
    for a in decision.assessments:
        eta = f"{a.eta_seconds / 60:6.1f}m" if a.eta_seconds else "     —"
        mark = "*" if a.tier is decision.tier else " "
        print(f" {mark} {a.tier.value:<14} {eta}  {'eligible' if a.eligible else 'no'}")
        print(f"      {a.reason}")
    print()

    remaining = ledger.remaining("groq")
    print(f"groq budget today: {remaining.tokens:,} tokens ({remaining.tokens_source}), "
          f"{remaining.requests:,} requests ({remaining.requests_source})")

    if decision.banner:
        print(f"\n!! {decision.banner}\n")

    if spend_pages <= 0:
        print("\n(no --spend given, so nothing was called and nothing was spent)")
        return 0

    # ── the honesty check ────────────────────────────────────────────────────
    pages = spots.pages_by_density()[:spend_pages]
    by_number = {p.number: p for p in doc.pages}
    predicted = estimate_document(spots, page_budget=spend_pages)

    print(f"\nextracting pages {pages} on {decision.tier.value} to check the estimate")
    print(f"predicted: {predicted.describe()}\n")

    gateway = build_gateway(decision.tier)
    context = await read_document_context(doc, gateway)
    print(f"  context: entity={context.entity!r} scale={context.reporting_scale!r} "
          f"currency={context.reporting_currency!r} basis={context.default_basis.value}")

    from uuid import uuid4

    doc_id, run_id = uuid4(), uuid4()
    total_grounded = 0
    for number in pages:
        claims = await extract_page(
            by_number[number], gateway, document_id=doc_id, context=context, run_id=run_id
        )
        grounded, quarantined, _ = verify_all(claims, {number: by_number[number]})
        total_grounded += len(grounded)
        print(f"  p{number:<4} {len(grounded):3} grounded, {len(quarantined)} quarantined")

    after = ledger.usage_today("groq")
    actual_tokens = after.tokens - before.tokens
    actual_requests = after.requests - before.requests

    print()
    if decision.tier is not Tier.GROQ or actual_tokens == 0:
        print(f"{total_grounded} claims grounded. No Groq usage recorded "
              f"(tier was {decision.tier.value}), so there is nothing to calibrate against.")
        return 0

    error = (predicted.total_tokens - actual_tokens) / actual_tokens * 100
    print("estimate vs invoice")
    print("-" * 72)
    print(f"  tokens     predicted {predicted.total_tokens:>8,}   billed {actual_tokens:>8,}   "
          f"{error:+.1f}%")
    print(f"  requests   predicted {predicted.requests:>8,}   made   {actual_requests:>8,}")
    observed = after.chars_per_token
    if observed:
        print(f"  chars/token  prior {predicted.chars_per_token:.2f}   observed {observed:.2f}")
        print("  (the observed figure is now used by every subsequent routing decision)")
    verdict = "conservative — safe" if error > 0 else "optimistic — the margin absorbs this"
    print(f"\n  {verdict}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spend = 0
    if "--spend" in sys.argv:
        i = sys.argv.index("--spend")
        spend = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 1
    if not args:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(args[0], spend)))
