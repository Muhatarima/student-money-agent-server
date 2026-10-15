from dataclasses import replace
from decimal import Decimal

import pytest

from app.config import settings
from app.policy import QUOTE_MIN_SECONDS_LEFT, PolicyContext, evaluate

D = Decimal
CENT = D("0.01")


def ok_ctx(**changes) -> PolicyContext:
    """A normal, allowed one-off transfer. Each test changes one thing."""
    ctx = PolicyContext(
        user_frozen=False,
        amount_bdt=D("1000"),
        spent_today_bdt=D("0"),
        spent_this_month_for_mandate=None,
        mandate_max_per_month=None,
        rate_age_seconds=10,
        quote_expires_in_seconds=60,
        recipient_cooling_active=False,
        mandate_active=None,
        mandate_valid_until_passed=None,
        currency_pair_supported=True,
    )
    return replace(ctx, **changes)


def mandate_ctx(**changes) -> PolicyContext:
    """A normal, allowed transfer coming from an active auto-rule."""
    defaults = {
        "mandate_active": True,
        "mandate_valid_until_passed": False,
        "spent_this_month_for_mandate": D("0"),
        "mandate_max_per_month": D("5000"),
    }
    return ok_ctx(**(defaults | changes))


def denied_for(ctx) -> list[str]:
    decision = evaluate(ctx)
    assert decision.allowed is (not decision.reasons)
    return decision.reasons


def test_normal_transfer_is_allowed():
    assert evaluate(ok_ctx()).allowed
    assert evaluate(ok_ctx()).reasons == []


def test_normal_mandate_transfer_is_allowed():
    assert evaluate(mandate_ctx()).allowed


def test_no_quote_yet_is_allowed():
    # Policy runs before the quote exists too
    assert evaluate(ok_ctx(quote_expires_in_seconds=None)).allowed


# --- One test per rule ---


def test_frozen_is_denied():
    assert denied_for(ok_ctx(user_frozen=True)) == ["account_frozen"]


def test_unsupported_pair_is_denied():
    assert denied_for(ok_ctx(currency_pair_supported=False)) == ["pair_not_supported"]


def test_recipient_cooling_is_denied():
    assert denied_for(ok_ctx(recipient_cooling_active=True)) == ["recipient_cooling_period"]


@pytest.mark.parametrize("amount", [D("0"), D("-1")])
def test_zero_or_negative_amount_is_denied(amount):
    assert denied_for(ok_ctx(amount_bdt=amount)) == ["invalid_amount"]


def test_over_transaction_limit_is_denied():
    ctx = ok_ctx(amount_bdt=settings.max_per_tx_bdt + CENT)
    assert "over_transaction_limit" in denied_for(ctx)


def test_over_daily_limit_is_denied():
    ctx = ok_ctx(spent_today_bdt=settings.max_per_day_bdt - D("999.99"))
    assert denied_for(ctx) == ["over_daily_limit"]


def test_stale_rate_is_denied():
    ctx = ok_ctx(rate_age_seconds=settings.rate_stale_seconds + 1)
    assert denied_for(ctx) == ["rate_stale"]


@pytest.mark.parametrize("seconds", [QUOTE_MIN_SECONDS_LEFT, 0, -30])
def test_quote_about_to_expire_is_denied(seconds):
    assert denied_for(ok_ctx(quote_expires_in_seconds=seconds)) == ["quote_expired"]


def test_inactive_rule_is_denied():
    assert denied_for(mandate_ctx(mandate_active=False)) == ["rule_not_active"]


def test_expired_rule_is_denied():
    assert denied_for(mandate_ctx(mandate_valid_until_passed=True)) == ["rule_expired"]


def test_over_rule_monthly_limit_is_denied():
    ctx = mandate_ctx(spent_this_month_for_mandate=D("4000.01"))
    assert denied_for(ctx) == ["over_rule_monthly_limit"]


# --- Exactly at the limit is allowed; one cent over is denied ---


def test_everything_at_its_limit_is_allowed():
    ctx = mandate_ctx(
        amount_bdt=settings.max_per_tx_bdt,
        spent_today_bdt=settings.max_per_day_bdt - settings.max_per_tx_bdt,
        spent_this_month_for_mandate=D("0"),
        mandate_max_per_month=settings.max_per_tx_bdt,
        rate_age_seconds=settings.rate_stale_seconds,
        quote_expires_in_seconds=QUOTE_MIN_SECONDS_LEFT + 1,
    )
    assert evaluate(ctx).allowed


def test_one_cent_over_every_limit_is_denied():
    ctx = mandate_ctx(
        amount_bdt=settings.max_per_tx_bdt + CENT,
        spent_today_bdt=settings.max_per_day_bdt - settings.max_per_tx_bdt,
        spent_this_month_for_mandate=D("0"),
        mandate_max_per_month=settings.max_per_tx_bdt,
    )
    assert denied_for(ctx) == [
        "over_transaction_limit",
        "over_daily_limit",
        "over_rule_monthly_limit",
    ]


def test_every_failing_reason_is_collected():
    ctx = ok_ctx(user_frozen=True, currency_pair_supported=False, rate_age_seconds=9999)
    assert denied_for(ctx) == ["account_frozen", "pair_not_supported", "rate_stale"]


# --- Fail closed: unknown values mean NO ---


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("user_frozen", "account_frozen"),
        ("currency_pair_supported", "pair_not_supported"),
        ("recipient_cooling_active", "recipient_cooling_period"),
        ("amount_bdt", "invalid_amount"),
        ("spent_today_bdt", "over_daily_limit"),
        ("rate_age_seconds", "rate_stale"),
    ],
)
def test_unknown_required_value_is_denied(field, reason):
    assert reason in denied_for(ok_ctx(**{field: None}))


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("mandate_active", "rule_not_active"),
        ("mandate_valid_until_passed", "rule_expired"),
        ("spent_this_month_for_mandate", "over_rule_monthly_limit"),
        ("mandate_max_per_month", "over_rule_monthly_limit"),
    ],
)
def test_unknown_mandate_value_is_denied(field, reason):
    assert reason in denied_for(mandate_ctx(**{field: None}))


def test_any_mandate_field_turns_on_all_mandate_checks():
    # Only one rule field filled in: the rest are unknown, so it must be denied
    ctx = ok_ctx(mandate_max_per_month=D("5000"))
    assert denied_for(ctx) == ["rule_not_active", "rule_expired", "over_rule_monthly_limit"]
