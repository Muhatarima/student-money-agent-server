"""Notifications for Messaging (A). Never put phone numbers, wa_id or document data in here."""

import logging
import uuid

from contracts.events import NotifyEvent

from app.redis_client import redis

log = logging.getLogger(__name__)

STREAM = "events:notify"


async def publish(type: str, user_id: uuid.UUID, data: dict | None = None) -> None:
    # Called after the DB change is committed: a lost notification must not undo it
    try:
        event = NotifyEvent(type=type, user_id=user_id, data=data or {})
        await redis.xadd(STREAM, {"data": event.model_dump_json()})
    except Exception:
        log.exception("could not publish %s event", type)
