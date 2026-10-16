"""Tests for packages/contracts. They live here, outside the package, so the shared contracts
stay exactly as agreed (changing that folder needs "contract change approved").

Run from the repo root: pip install ./packages/contracts pytest, then pytest -q tests/contracts
"""

import uuid
from decimal import Decimal

import pytest
from contracts.events import NotifyEvent
from contracts.hashing import canonical_json, payload_hash
from contracts.models import TransferProposal
from pydantic import ValidationError


def test_payload_hash_ignores_key_order():
    # Money and Identity build the same summary in different orders: the hash must match
    assert payload_hash({"amount": "1000.00", "rate": "135.20"}) == payload_hash(
        {"rate": "135.20", "amount": "1000.00"}
    )


def test_payload_hash_known_answer():
    # Pins the exact algorithm: sha256 of compact, sorted JSON. If this changes, every
    # stored approval signature stops matching.
    summary = {"rate": "135.20", "amount": "1000.00"}
    assert canonical_json(summary) == '{"amount":"1000.00","rate":"135.20"}'
    assert payload_hash(summary) == (
        "bbf28fcbf9622d8ef3f08a2f67d38bf1f0d30e2e735ad99b2d0a474926ef843c"
    )


def test_payload_hash_changes_when_one_digit_changes():
    assert payload_hash({"amount": "1000.00"}) != payload_hash({"amount": "1000.01"})


def proposal(**changes):
    fields = {
        "user_id": uuid.uuid4(),
        "from_currency": "BDT",
        "to_currency": "IDR",
        "amount": "1000.00",
        "idempotency_key": "msg123:0",
    }
    return TransferProposal(**(fields | changes))


def test_amount_is_a_decimal():
    assert proposal().amount == Decimal("1000.00")


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},  # must be more than 0
        {"amount": "10.001"},  # at most 2 decimal places
        {"from_currency": "EUR"},  # only BDT, IDR, USD
        {"idempotency_key": "short"},  # at least 8 characters
    ],
)
def test_bad_proposals_are_rejected(changes):
    with pytest.raises(ValidationError):
        proposal(**changes)


def test_notify_event_round_trip():
    event = NotifyEvent(type="frozen", user_id=uuid.uuid4(), data={})
    assert NotifyEvent.model_validate_json(event.model_dump_json()) == event
