"""Provider routing: what a document will cost, what is left, and which tier wins."""

from core.route.estimate import TokenEstimate, estimate_document
from core.route.ledger import FREE_TIER, DayUsage, Remaining, TokenLedger
from core.route.router import (
    DETERMINISTIC_BANNER,
    RoutingDecision,
    TierAssessment,
    plan_pages,
    probe_ollama,
    route_document,
)

__all__ = [
    "DETERMINISTIC_BANNER",
    "DayUsage",
    "FREE_TIER",
    "Remaining",
    "RoutingDecision",
    "TierAssessment",
    "TokenEstimate",
    "TokenLedger",
    "estimate_document",
    "plan_pages",
    "probe_ollama",
    "route_document",
]
