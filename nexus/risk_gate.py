"""Independent paper-order admission gate.

All inputs are untrusted research assertions, not market observations.  Gate
approval is not evidence of profit and never grants brokerage execution
authority.  The public result must remain finite and JSON-safe even when a
caller supplies adversarial Python values through the direct API.
"""
from decimal import Decimal, InvalidOperation
import math

from nexus.paper_execution import TIERS, quote_age_ok


MAX_QUANTITY = Decimal("1e12")
MAX_BPS = Decimal("1e6")
BOOLEAN_FIELDS = frozenset(("data_verified", "instrument_authorized",
    "market_open", "account_reconciled", "emergency_halt",
    "cooldown_active"))
QUANTITY_FIELDS = frozenset(("equity", "notional", "current_exposure",
    "realized_daily_loss"))
BPS_FIELDS = frozenset(("roundtrip_fees_bps", "spread_bps",
    "roundtrip_slippage_bps", "conservative_expected_edge_bps",
    "modeled_worst_loss_bps"))
REQUEST_FIELDS = frozenset((*BOOLEAN_FIELDS, *QUANTITY_FIELDS, *BPS_FIELDS,
    "quote_timestamp", "tier"))


def number(value, *, maximum):
    """Return one exact, finite, bounded built-in numeric value."""
    if type(value) not in (int, float, Decimal):
        raise ValueError("Malformed quantity")
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("Malformed quantity") from None
    if not result.is_finite() or result.copy_abs() > maximum:
        raise ValueError("Non-finite or unbounded quantity")
    return result


def evaluate(request):
    reject = []
    if (type(request) is not dict or
            any(type(key) is not str for key in request) or
            set(request) != REQUEST_FIELDS):
        reject.append("INVALID_REQUEST_SCHEMA")
        # Never invoke polymorphic mapping methods after rejecting a mapping
        # subclass. Exact dictionaries with missing/extra fields can still be
        # evaluated conservatively so every failed claim remains visible.
        request = request if type(request) is dict else {}

    if request.get("data_verified") is not True:
        reject.append("UNVERIFIED_MARKET_DATA")
    if request.get("instrument_authorized") is not True:
        reject.append("INSTRUMENT_NOT_AUTHORIZED")
    if request.get("market_open") is not True:
        reject.append("MARKET_NOT_OPEN")
    if request.get("account_reconciled") is not True:
        reject.append("ACCOUNT_NOT_RECONCILED")
    if request.get("emergency_halt") is not False:
        reject.append("EMERGENCY_HALT_OR_UNKNOWN")
    if request.get("cooldown_active") is not False:
        reject.append("COOLDOWN_ACTIVE_OR_UNKNOWN")

    quote_timestamp = request.get("quote_timestamp")
    if (type(quote_timestamp) not in (int, float) or
            not math.isfinite(quote_timestamp) or
            not quote_age_ok(quote_timestamp)):
        reject.append("STALE_OR_MISSING_QUOTE")

    tier = request.get("tier")
    tier_supported = type(tier) is str and tier in TIERS
    if not tier_supported:
        reject.append("UNSUPPORTED_TIER")

    try:
        equity = number(request.get("equity"), maximum=MAX_QUANTITY)
        notional = number(request.get("notional"), maximum=MAX_QUANTITY)
        exposure = number(request.get("current_exposure"),
            maximum=MAX_QUANTITY)
        daily_loss = number(request.get("realized_daily_loss"),
            maximum=MAX_QUANTITY)
        fee_bps = number(request.get("roundtrip_fees_bps"), maximum=MAX_BPS)
        spread_bps = number(request.get("spread_bps"), maximum=MAX_BPS)
        slippage_bps = number(request.get("roundtrip_slippage_bps"),
            maximum=MAX_BPS)
        forecast_bps = number(request.get("conservative_expected_edge_bps"),
            maximum=MAX_BPS)
        worst_loss_bps = number(request.get("modeled_worst_loss_bps"),
            maximum=MAX_BPS)
        if equity <= 0 or notional <= 0 or exposure < 0 or daily_loss < 0:
            reject.append("INVALID_ACCOUNT_VALUES")
        if min(fee_bps, spread_bps, slippage_bps, worst_loss_bps) < 0:
            reject.append("INVALID_COST_ESTIMATE")
        total_cost = fee_bps + spread_bps + slippage_bps
        # Require a cushion of at least another 50% of modeled costs + 10 bps.
        if forecast_bps <= total_cost * Decimal("1.5") + Decimal(10):
            reject.append("INSUFFICIENT_NET_EDGE_AFTER_COSTS")
        if worst_loss_bps > 250:
            reject.append("EXCESSIVE_SCENARIO_LOSS")
        if daily_loss >= equity * Decimal(".01"):
            reject.append("DAILY_LOSS_BUDGET_EXHAUSTED")
        tier_limit = TIERS[tier] if tier_supported else Decimal(0)
        if equity > 0 and notional > equity * tier_limit:
            reject.append("TIER_SIZE_CAP")
        if equity > 0 and exposure + notional > equity * Decimal(".10"):
            reject.append("PORTFOLIO_EXPOSURE_CAP")
        if (equity > 0 and
                notional * worst_loss_bps / Decimal(10000) >
                equity * Decimal(".001")):
            reject.append("SCENARIO_LOSS_BUDGET")
    except (ValueError, InvalidOperation):
        total_cost = None
        reject.append("MISSING_OR_INVALID_NUMERIC_EVIDENCE")

    return {
        "decision": "REJECT" if reject else "PAPER_RESEARCH_CANDIDATE",
        "reasons": sorted(set(reject)),
        "live_execution_authorized": False,
        "simulation_only": True,
        "cost_bps": float(total_cost) if total_cost is not None else None,
        "meaning": ("Admission gate for further paper research only. "
            "Estimates cannot guarantee outcomes."),
    }
