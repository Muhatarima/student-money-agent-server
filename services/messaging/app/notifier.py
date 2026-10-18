"""Turns events from Money (B) and Identity (C) into WhatsApp messages.

Run with: python -m app.notifier
Reads stream events:notify as consumer group "messaging". An event is acked (XACK) only
after the WhatsApp send worked; a failed one is retried, and after 3 tries it goes to
queue:notify_dead so one broken event can't block the rest.
"""

import asyncio
import json
import logging

from contracts.events import NotifyEvent
from pydantic import ValidationError
from redis.exceptions import ResponseError

from app import whatsapp
from app.clients import IdentityClient
from app.logs import setup_logging
from app.redis_client import redis

log = logging.getLogger(__name__)

STREAM = "events:notify"
GROUP = "messaging"
CONSUMER = "notifier"
DEAD = "queue:notify_dead"
BLOCK_MS = 20000  # must stay below the Redis client's socket_timeout (30s)
MAX_FAILURES = 3

identity = IdentityClient()

# Reason codes from Money, in words a student understands. Unknown codes are shown as-is.
REASONS = {
    "account_frozen": "your account is frozen",
    "user_frozen": "your account is frozen",
    "global_freeze": "all transfers are paused for safety right now",
    "insufficient_funds": "there isn't enough money in your wallet",
    "quote_expired": "the price expired before it could be sent",
    "over_transaction_limit": "it's over the limit for one transfer",
    "over_daily_limit": "it's over your daily limit",
    "over_rule_monthly_limit": "the rule's monthly limit is reached",
    "rate_stale": "we couldn't get a fresh rate",
    "RateStale": "we couldn't get a fresh rate",
    "ValueError": "the amount is too small to cover the fee",
    "provider_rejected": "the provider declined it",
    "provider_failed": "the provider couldn't complete it",
}


def friendly(reasons: str) -> str:
    # Money sends one code or several joined with ", "
    words = [REASONS.get(code, code.replace("_", " ")) for code in reasons.split(", ")]
    return " and ".join(words)


async def deliver(to: str, event: NotifyEvent) -> bool:
    """Send the message for this event. False = we don't know this event type."""
    d = event.data
    match event.type:
        case "approval_needed":
            await whatsapp.send_link_button(
                to, "Please review and approve", "Review & approve", d["url"]
            )
        case "transfer_settled":
            await whatsapp.send_text(
                to,
                f"✅ Converted {d['amount']} {d['from_currency']} → {d['amount_out']} "
                f"{d['to_currency']} at {d['rate']}. Fee {d['fee']} {d['from_currency']}.",
            )
        case "transfer_failed":
            reason = friendly(d.get("reason", "unknown"))
            await whatsapp.send_text(to, f"❌ Transfer failed: {reason}. No money was moved.")
        case "rule_triggered":
            state = d.get("state", "").lower()
            await whatsapp.send_text(to, f"⚡ Your auto-rule ran. The conversion is {state}.")
        case "rule_skipped":
            reason = friendly(d.get("reason", "unknown"))
            await whatsapp.send_text(
                to, f"⏭️ Your auto-rule didn't run: {reason}. No money was moved."
            )
        case "rule_paused":
            await whatsapp.send_text(
                to,
                "⏸️ Your auto-rule was paused because we couldn't verify your approval. "
                "No money was moved. Create the rule again to restart it.",
            )
        case "account_linked":
            await whatsapp.send_text(to, "🔐 Account secured with your passkey!")
        case "frozen":
            # Identity and Money each publish this, and the FREEZE handler has already
            # confirmed to the user. Sending here too would mean 3 messages for one FREEZE.
            pass
        case _:
            return False
    return True


async def process(entry_id: str, fields: dict) -> bool:
    """Handle one stream entry. Returns False if the send failed (the entry stays pending)."""
    try:
        event = NotifyEvent.model_validate_json((fields or {})["data"])  # None if XDEL-ed
    except (KeyError, ValidationError):
        log.warning("notify %s: unreadable event, skipped", entry_id)
        await redis.xack(STREAM, GROUP, entry_id)
        return True

    try:
        to = await identity.get_wa_target(event.user_id)
        known = await deliver(to, event)
    except Exception as e:
        await failed(entry_id, fields, e)
        return False

    if not known:
        log.warning("notify %s: unknown event type %r, skipped", entry_id, event.type)
    await redis.xack(STREAM, GROUP, entry_id)
    return True


async def failed(entry_id: str, fields: dict, error: Exception) -> None:
    failures_key = f"queue:failures:notify:{entry_id}"
    failures = await redis.incr(failures_key)
    await redis.expire(failures_key, 86400)
    if failures < MAX_FAILURES:
        log.warning("notify %s failed (%s), will retry", entry_id, type(error).__name__)
        return
    log.error("notify %s failed %d times, moved to %s", entry_id, failures, DEAD)
    await redis.lpush(DEAD, json.dumps({"id": entry_id, "data": fields.get("data")}))
    await redis.xack(STREAM, GROUP, entry_id)
    await redis.delete(failures_key)


async def ensure_group() -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):  # BUSYGROUP = the group already exists, that's fine
            raise


async def run_once(block_ms: int | None = BLOCK_MS) -> bool:
    """One round. Returns False if any send failed. First our own unacked entries
    (failed sends, or a crash before XACK), then new ones."""
    ok = True
    for start, block in (("0", None), (">", block_ms)):
        for _stream, entries in await redis.xreadgroup(
            GROUP, CONSUMER, {STREAM: start}, count=10, block=block
        ):
            for entry_id, fields in entries:
                ok = await process(entry_id, fields) and ok
        if not ok:
            break  # retry the pending ones first
    return ok


async def main() -> None:
    await ensure_group()
    log.info("notifier started, waiting for events...")
    while True:
        try:
            if not await run_once():
                await asyncio.sleep(2)  # don't hammer a service that is down
        except Exception:
            log.exception("notifier round failed, retrying in 2s")
            await asyncio.sleep(2)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
