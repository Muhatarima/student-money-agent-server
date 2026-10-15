"""Notifications for A (WhatsApp). Never put phone numbers, names or document data in here."""

import uuid

from contracts.events import NotifyEvent
from redis.asyncio import Redis

STREAM = "events:notify"


async def publish(redis: Redis, type: str, user_id: uuid.UUID, data: dict) -> None:
    event = NotifyEvent(type=type, user_id=user_id, data=data)
    await redis.xadd(STREAM, {"data": event.model_dump_json()})
