import json
import uuid

import httpx
import pytest

from app import notifier, whatsapp
from app.clients import IdentityClient, ServiceUnavailable
from app.config import settings
from tests.mocks import mock_identity

USER = uuid.uuid4()
WA_ID = "8801700001234"  # what the mock identity returns


@pytest.fixture(autouse=True)
async def setup(monkeypatch):
    monkeypatch.setattr(settings, "wa_fake_send", True)
    monkeypatch.setattr(
        notifier, "identity", IdentityClient(transport=httpx.ASGITransport(app=mock_identity.app))
    )
    whatsapp.fake_outbox.clear()
    await notifier.ensure_group()


async def publish(type_, data=None, user_id=USER):
    # Same shape B and C publish: one field "data" with NotifyEvent JSON
    event = {"type": type_, "user_id": str(user_id), "data": data or {}}
    await notifier.redis.xadd(notifier.STREAM, {"data": json.dumps(event)})


async def pending_count():
    return (await notifier.redis.xpending(notifier.STREAM, notifier.GROUP))["pending"]


def sent_text():
    [message] = whatsapp.fake_outbox
    assert message["to"] == WA_ID
    return message["text"]["body"]


SETTLED = {
    "transfer_id": "t1",
    "amount": "1000.00",
    "from_currency": "BDT",
    "to_currency": "IDR",
    "amount_out": "132300.00",
    "rate": "135.00000000",
    "fee": "20.00",
}


@pytest.mark.parametrize(
    ("type_", "data", "expected"),
    [
        ("transfer_settled", SETTLED, "✅ Converted 1000.00 BDT → 132300.00 IDR at 135.00000000."),
        ("transfer_failed", {"reason": "insufficient_funds"}, "isn't enough money"),
        ("rule_triggered", {"rule_id": "r1", "state": "SETTLED"}, "conversion is settled"),
        ("rule_skipped", {"rule_id": "r1", "reason": "over_rule_monthly_limit"}, "monthly limit"),
        ("rule_skipped", {"rule_id": "r1", "reason": "RateStale"}, "fresh rate"),
        ("rule_paused", {"rule_id": "r1"}, "paused"),
        ("account_linked", {}, "passkey"),
    ],
)
async def test_event_becomes_a_whatsapp_message(type_, data, expected):
    await publish(type_, data)
    assert await notifier.run_once(block_ms=None)
    assert expected in sent_text()
    assert await pending_count() == 0  # acked


async def test_frozen_event_is_acked_without_a_message():
    # Identity AND Money both publish "frozen"; the handler already confirmed the freeze
    # to the user, so the notifier must not send anything (one FREEZE = one message)
    await publish("frozen")
    await publish("frozen")
    assert await notifier.run_once(block_ms=None)
    assert whatsapp.fake_outbox == []
    assert await pending_count() == 0


async def test_several_reasons_are_all_shown():
    await publish("transfer_failed", {"reason": "over_daily_limit, rate_stale"})
    await notifier.run_once(block_ms=None)
    assert "daily limit and we couldn't get a fresh rate" in sent_text()


async def test_unknown_reason_code_is_shown_readably():
    await publish("rule_skipped", {"reason": "something_new"})
    await notifier.run_once(block_ms=None)
    assert "something new" in sent_text()


async def test_approval_needed_sends_link_button():
    await publish("approval_needed", {"url": "http://id.test/approve/abc"})
    await notifier.run_once(block_ms=None)
    [message] = whatsapp.fake_outbox
    assert message["interactive"]["action"]["parameters"]["url"] == "http://id.test/approve/abc"


async def test_unknown_type_is_acked_and_nothing_sent():
    await publish("something_else")
    assert await notifier.run_once(block_ms=None)
    assert whatsapp.fake_outbox == []
    assert await pending_count() == 0


async def test_unreadable_event_is_acked_and_nothing_sent():
    await notifier.redis.xadd(notifier.STREAM, {"data": "not json"})
    await notifier.redis.xadd(notifier.STREAM, {"other": "x"})
    assert await notifier.run_once(block_ms=None)
    assert whatsapp.fake_outbox == []
    assert await pending_count() == 0


async def test_failed_send_is_not_acked_and_retried(monkeypatch):
    calls = {"n": 0}
    real_send = whatsapp.send_text

    async def flaky(to, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Meta down")
        await real_send(to, text)

    monkeypatch.setattr(whatsapp, "send_text", flaky)
    await publish("account_linked")
    assert not await notifier.run_once(block_ms=None)
    assert await pending_count() == 1  # not acked: the send didn't happen
    assert await notifier.run_once(block_ms=None)  # retried from the pending list
    assert len(whatsapp.fake_outbox) == 1
    assert await pending_count() == 0


async def test_third_failure_goes_to_dead_list(monkeypatch):
    async def broken(to, text):
        raise RuntimeError("Meta down")

    monkeypatch.setattr(whatsapp, "send_text", broken)
    await publish("account_linked")
    for _ in range(3):
        assert not await notifier.run_once(block_ms=None)
    assert await pending_count() == 0  # not stuck forever
    [dead] = await notifier.redis.lrange(notifier.DEAD, 0, -1)
    assert '"account_linked"' in json.loads(dead)["data"]


async def test_identity_down_is_retried_later(monkeypatch):
    async def down(user_id):
        raise ServiceUnavailable("identity unreachable")

    monkeypatch.setattr(notifier.identity, "get_wa_target", down)
    await publish("account_linked")
    assert not await notifier.run_once(block_ms=None)
    assert await pending_count() == 1


async def test_logs_never_contain_phone_or_text(capsys, caplog):
    await publish("transfer_settled", SETTLED)
    await notifier.run_once(block_ms=None)
    logs = capsys.readouterr().out + caplog.text
    assert WA_ID not in logs and "132300" not in logs


async def test_ensure_group_twice_is_fine():
    await notifier.ensure_group()  # BUSYGROUP is ignored
