import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from contracts.models import ApprovalCreated, TransferProposal
from fakeredis import FakeAsyncRedis
from sqlalchemy import pool, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import identity_client, main, proposals
from app.config import settings
from app.db import get_session
from app.models import Freeze, Quote, Recipient, Transfer, TransferState
from app.providers.mock import MockProvider

D = Decimal
S = TransferState
KEY = {"X-Internal-Key": "test-key"}


class FakeIdentity:
    def __init__(self, fail=False):
        self.fail = fail
        self.approvals = []  # what B sent to C
        self.ids = {}  # subject_id -> approval_id

    async def create_approval(self, data):
        if self.fail:
            raise httpx.ConnectError("identity down")
        self.approvals.append(data)
        approval_id = uuid.uuid4()
        self.ids[str(data.subject_id)] = approval_id
        return ApprovalCreated(approval_id=approval_id, url=f"http://id.test/approve/{approval_id}")


@pytest.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    now = datetime.now(UTC).isoformat()
    for pair, rate in [("BDT_IDR", "135"), ("BDT_USD", "0.0082")]:
        await r.hset(f"rates:{pair}", mapping={"rate": rate, "fetched_at": now})
    return r


@pytest.fixture
def identity(monkeypatch):
    fake = FakeIdentity()
    monkeypatch.setattr(identity_client, "create_approval", fake.create_approval)
    return fake


@pytest.fixture
def provider():
    return MockProvider(failure_rate=0, timeout_rate=0)


@pytest.fixture
async def client(session, redis, identity, provider, monkeypatch):
    async def test_session():
        yield session

    @asynccontextmanager
    async def background_session():
        yield session

    main.app.dependency_overrides[get_session] = test_session
    monkeypatch.setattr(main, "redis", redis)
    monkeypatch.setattr(main, "provider", provider)
    monkeypatch.setattr(main, "new_session", background_session)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://money", headers=KEY) as c:
        yield c
    main.app.dependency_overrides.clear()


def transfer_body(user_id, amount="1000", **changes):
    return {
        "user_id": str(user_id),
        "from_currency": "BDT",
        "to_currency": "IDR",
        "amount": amount,
        "idempotency_key": f"test-{uuid.uuid4()}",
    } | changes


async def propose(client, user_id, **kwargs):
    response = await client.post(
        "/internal/proposals/transfer", json=transfer_body(user_id, **kwargs)
    )
    assert response.status_code == 200
    return response.json()


def confirm_body(identity, transfer_id, payload_hash=None):
    sent = identity.approvals[-1]
    return {
        "approval_id": str(identity.ids[str(transfer_id)]),
        "subject_id": str(transfer_id),
        "payload_hash": payload_hash or sent.payload_hash,
        "signature_id": str(uuid.uuid4()),
    }


# --- Happy path (the whole Day 4 flow) ---


async def test_happy_path_to_settled(client, session, identity, provider, redis):
    user_id = uuid.uuid4()
    await client.post(f"/dev/seed/{user_id}", params={"amount": "5000"})

    result = await propose(client, user_id)
    assert result["status"] == "pending_approval"
    assert result["approval_url"].startswith("http://id.test/approve/")
    transfer_id = result["subject_id"]

    # What C received: everything the user needs to see, and its hash
    sent = identity.approvals[-1]
    assert sent.kind == "transfer"
    assert set(sent.summary) == {
        "from_currency",
        "to_currency",
        "amount",
        "rate",
        "fee",
        "amount_out",
        "expires_at",
        "provider",
    }

    response = await client.post(
        "/internal/approvals/confirmed", json=confirm_body(identity, transfer_id)
    )
    assert response.status_code == 200

    balance = (await client.get(f"/internal/balance/{user_id}")).json()["balances"]
    assert D(balance["BDT"]) == D("4000")
    assert D(balance["IDR"]) == D("132300")  # (1000 - 20 fee) * 135
    assert len(provider.submit_calls) == 1
    types = [f["data"] for _, f in await redis.xrange("events:notify")]
    assert '"approval_needed"' in types[0] and '"transfer_settled"' in types[-1]


async def test_same_proposal_twice_gives_one_approval(client, identity):
    body = transfer_body(uuid.uuid4())
    first = (await client.post("/internal/proposals/transfer", json=body)).json()
    second = (await client.post("/internal/proposals/transfer", json=body)).json()
    assert first["subject_id"] == second["subject_id"]
    assert second["status"] == "pending_approval"
    assert len(identity.approvals) == 1


# --- Rejections ---


async def test_over_limit_is_rejected(client, identity):
    result = await propose(client, uuid.uuid4(), amount=str(settings.max_per_tx_bdt + 1))
    assert result["status"] == "rejected"
    assert result["reason"] == "over_transaction_limit"
    assert identity.approvals == []  # never even asked the user


async def test_over_daily_limit_is_rejected(client):
    user_id = uuid.uuid4()
    for _ in range(2):
        assert (await propose(client, user_id, amount="50000"))["status"] == "pending_approval"
    result = await propose(client, user_id, amount="100")
    assert result["reason"] == "over_daily_limit"


async def test_proposal_rejected_before_quote_does_not_count_today(client, redis):
    # It stayed CREATED and can never move money, so it must not use up the daily limit
    user_id = uuid.uuid4()
    rate = await redis.hgetall("rates:BDT_IDR")
    await redis.delete("rates:BDT_IDR")
    assert (await propose(client, user_id, amount="50000"))["reason"] == "rate_unavailable"
    await redis.hset("rates:BDT_IDR", mapping=rate)
    for _ in range(2):
        assert (await propose(client, user_id, amount="50000"))["status"] == "pending_approval"


async def test_approval_with_expired_quote_does_not_count_today(client, session):
    # Nobody can approve it any more, so it must not use up the daily limit
    user_id = uuid.uuid4()
    for _ in range(2):
        assert (await propose(client, user_id, amount="50000"))["status"] == "pending_approval"
    past = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(update(Quote).values(expires_at=past))
    assert (await propose(client, user_id, amount="50000"))["status"] == "pending_approval"


async def test_frozen_user_is_rejected(client, session):
    user_id = uuid.uuid4()
    session.add(Freeze(user_id=user_id))
    await session.flush()
    assert (await propose(client, user_id))["reason"] == "account_frozen"


async def test_stale_rate_is_rejected(client, redis):
    await redis.delete("rates:BDT_IDR")
    assert (await propose(client, uuid.uuid4()))["reason"] == "rate_unavailable"


async def test_unsupported_pair_is_rejected(client):
    result = await propose(client, uuid.uuid4(), from_currency="IDR", to_currency="BDT")
    assert result["reason"] == "pair_not_supported"


async def test_amount_below_fee_is_rejected(client):
    assert (await propose(client, uuid.uuid4(), amount="20"))["reason"] == "amount_too_small"


async def test_unknown_recipient_is_rejected(client):
    result = await propose(client, uuid.uuid4(), recipient_id=str(uuid.uuid4()))
    assert result["reason"] == "unknown_recipient"


async def test_someone_elses_recipient_is_rejected(client, session):
    other = Recipient(
        user_id=uuid.uuid4(),
        provider_token="tok",
        masked_label="****1234",
        cooling_until=datetime.now(UTC),
    )
    session.add(other)
    await session.flush()
    result = await propose(client, uuid.uuid4(), recipient_id=str(other.id))
    assert result["reason"] == "unknown_recipient"


async def test_recipient_in_cooling_period_is_rejected(client, session):
    user_id = uuid.uuid4()
    new = Recipient(
        user_id=user_id,
        provider_token="tok",
        masked_label="****1234",
        cooling_until=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(new)
    await session.flush()
    result = await propose(client, user_id, recipient_id=str(new.id))
    assert result["reason"] == "recipient_cooling_period"


async def test_identity_down_rejects_and_expires(client, session, identity):
    identity.fail = True
    result = await propose(client, uuid.uuid4())
    assert result["reason"] == "approval_unavailable"
    assert (await session.get(Transfer, uuid.UUID(result["subject_id"]))).state == S.EXPIRED


async def test_reused_key_with_different_amount_is_rejected(client):
    body = transfer_body(uuid.uuid4())
    await client.post("/internal/proposals/transfer", json=body)
    response = await client.post("/internal/proposals/transfer", json=body | {"amount": "999"})
    assert response.json()["reason"] == "idempotency_key_reused"


# --- Confirmation security ---


async def test_tampered_payload_hash_is_409(client, identity, provider):
    result = await propose(client, uuid.uuid4())
    body = confirm_body(identity, result["subject_id"], payload_hash="0" * 64)
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409
    assert provider.submit_calls == []


async def test_wrong_approval_id_is_409(client, identity, provider):
    result = await propose(client, uuid.uuid4())
    body = confirm_body(identity, result["subject_id"]) | {"approval_id": str(uuid.uuid4())}
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409
    assert provider.submit_calls == []


async def test_confirming_twice_is_409_the_second_time(client, identity, provider):
    user_id = uuid.uuid4()
    await client.post(f"/dev/seed/{user_id}")
    result = await propose(client, user_id)
    body = confirm_body(identity, result["subject_id"])
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 200
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409
    assert len(provider.submit_calls) == 1


async def test_expired_quote_at_confirmation_expires_and_never_calls_provider(
    client, session, identity, provider
):
    result = await propose(client, uuid.uuid4())
    transfer = await session.get(Transfer, uuid.UUID(result["subject_id"]))
    await session.execute(
        update(Quote)
        .where(Quote.id == transfer.quote_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    body = confirm_body(identity, result["subject_id"])
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409
    await session.refresh(transfer)
    assert transfer.state == S.EXPIRED
    assert provider.submit_calls == []


async def test_unknown_subject_is_404(client):
    body = {
        "approval_id": str(uuid.uuid4()),
        "subject_id": str(uuid.uuid4()),
        "payload_hash": "0" * 64,
        "signature_id": str(uuid.uuid4()),
    }
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 404


# --- Rules ---


def rule_body(user_id, **changes):
    return {
        "user_id": str(user_id),
        "from_currency": "BDT",
        "to_currency": "IDR",
        "target_rate": "136",
        "amount_per_run": "500",
        "max_per_month": "5000",
        "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "idempotency_key": f"rule-{uuid.uuid4()}",
    } | changes


async def test_rule_proposal_then_confirm_activates(client, identity, redis):
    user_id = uuid.uuid4()
    result = (await client.post("/internal/proposals/rule", json=rule_body(user_id))).json()
    assert result["status"] == "pending_approval"
    assert identity.approvals[-1].kind == "rule"

    body = confirm_body(identity, result["subject_id"])
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 200
    assert await redis.zscore("rules:BDT_IDR", result["subject_id"]) == 136.0  # indexed
    rules = (await client.get(f"/internal/rules/{user_id}")).json()
    assert [(r["pair"], D(r["target_rate"]), r["status"]) for r in rules] == [
        ("BDT_IDR", D("136"), "active")
    ]
    # Single-use
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409


async def test_rule_tampered_hash_is_409(client, identity):
    result = (await client.post("/internal/proposals/rule", json=rule_body(uuid.uuid4()))).json()
    body = confirm_body(identity, result["subject_id"], payload_hash="f" * 64)
    assert (await client.post("/internal/approvals/confirmed", json=body)).status_code == 409


async def test_same_rule_twice_is_one_mandate(client):
    body = rule_body(uuid.uuid4())
    first = (await client.post("/internal/proposals/rule", json=body)).json()
    second = (await client.post("/internal/proposals/rule", json=body)).json()
    assert first["subject_id"] == second["subject_id"]


async def test_rule_reused_key_with_different_target_is_rejected(client):
    body = rule_body(uuid.uuid4())
    await client.post("/internal/proposals/rule", json=body)
    second = await client.post("/internal/proposals/rule", json=body | {"target_rate": "200"})
    assert second.json()["reason"] == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"from_currency": "IDR", "to_currency": "BDT"}, "pair_not_supported"),
        ({"valid_until": "2020-01-01T00:00:00Z"}, "valid_until_in_past"),
    ],
)
async def test_bad_rule_is_rejected(client, changes, reason):
    response = await client.post(
        "/internal/proposals/rule", json=rule_body(uuid.uuid4(), **changes)
    )
    assert response.json()["reason"] == reason


# --- Every internal route checks the key ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/internal/balance/{uuid.uuid4()}"),
        ("GET", f"/internal/rules/{uuid.uuid4()}"),
        ("GET", "/internal/rates/BDT_IDR"),
        ("POST", "/internal/proposals/transfer"),
        ("POST", "/internal/proposals/rule"),
        ("POST", "/internal/approvals/confirmed"),
        ("POST", f"/dev/seed/{uuid.uuid4()}"),
        ("POST", f"/internal/users/{uuid.uuid4()}/freeze-money"),
    ],
)
@pytest.mark.parametrize("key", [None, "wrong-key"])
async def test_internal_routes_need_the_key(client, method, path, key):
    headers = {"X-Internal-Key": key} if key else {}
    client.headers.pop("X-Internal-Key", None)
    response = await client.request(method, path, headers=headers, json={})
    assert response.status_code == 401


async def test_freeze_money_route(client, session):
    user_id = uuid.uuid4()
    response = await client.post(f"/internal/users/{user_id}/freeze-money")
    assert response.json() == {"frozen": True}
    assert await session.get(Freeze, user_id) is not None


async def test_concurrent_proposals_cannot_beat_the_daily_limit(migrated_db, redis, identity):
    # Real race: separate DB connections. 3 x 40000 at once; the daily limit (100000) fits 2.
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    user_id = uuid.uuid4()

    async def propose_once():
        async with AsyncSession(engine, expire_on_commit=False) as session:  # like SessionLocal
            body = transfer_body(user_id, amount="40000")
            proposal = TransferProposal.model_validate(body)
            return (await proposals.propose_transfer(session, redis, proposal)).status

    try:
        results = await asyncio.gather(*[propose_once() for _ in range(3)])
    finally:
        await engine.dispose()
    assert sorted(results) == ["pending_approval", "pending_approval", "rejected"]
