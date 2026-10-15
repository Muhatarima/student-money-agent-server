"""Policy engine: plain rules that say yes or no. No DB, no network: everything is passed in.

Every rule must pass. A missing value where one is needed means NO (fail closed).
"""

from dataclasses import dataclass
from decimal import Decimal

from app.config import settings

QUOTE_MIN_SECONDS_LEFT = 5


@dataclass
class PolicyContext:
    user_frozen: bool
    amount_bdt: Decimal
    spent_today_bdt: Decimal
    spent_this_month_for_mandate: Decimal | None
    mandate_max_per_month: Decimal | None
    rate_age_seconds: int
    quote_expires_in_seconds: int | None
    recipient_cooling_active: bool
    mandate_active: bool | None
    mandate_valid_until_passed: bool | None
    currency_pair_supported: bool


@dataclass
class Decision:
    allowed: bool
    reasons: list[str]


def evaluate(ctx: PolicyContext) -> Decision:
    reasons = []

    if ctx.user_frozen is not False:
        reasons.append("account_frozen")
    if ctx.currency_pair_supported is not True:
        reasons.append("pair_not_supported")
    if ctx.recipient_cooling_active is not False:
        reasons.append("recipient_cooling_period")

    amount = ctx.amount_bdt
    if amount is None or amount <= 0:
        reasons.append("invalid_amount")
        amount = Decimal(0)  # so the limit checks below still run
    elif amount > settings.max_per_tx_bdt:
        reasons.append("over_transaction_limit")

    if ctx.spent_today_bdt is None or ctx.spent_today_bdt + amount > settings.max_per_day_bdt:
        reasons.append("over_daily_limit")

    if ctx.rate_age_seconds is None or ctx.rate_age_seconds > settings.rate_stale_seconds:
        reasons.append("rate_stale")

    if (
        ctx.quote_expires_in_seconds is not None
        and ctx.quote_expires_in_seconds <= QUOTE_MIN_SECONDS_LEFT
    ):
        reasons.append("quote_expired")

    # Mandate (auto-rule) checks: only when this transfer comes from a rule
    mandate_fields = [
        ctx.mandate_active,
        ctx.mandate_valid_until_passed,
        ctx.spent_this_month_for_mandate,
        ctx.mandate_max_per_month,
    ]
    if any(f is not None for f in mandate_fields):
        if ctx.mandate_active is not True:
            reasons.append("rule_not_active")
        if ctx.mandate_valid_until_passed is not False:
            reasons.append("rule_expired")
        if (
            ctx.spent_this_month_for_mandate is None
            or ctx.mandate_max_per_month is None
            or ctx.spent_this_month_for_mandate + amount > ctx.mandate_max_per_month
        ):
            reasons.append("over_rule_monthly_limit")

    return Decision(allowed=not reasons, reasons=reasons)
