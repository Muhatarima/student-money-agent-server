import uuid
from typing import Any

from contracts.events import NotifyEvent

from .redis_client import redis_client

EVENT_CHANNEL = "events:notify"


async def publish_event(
    event_type: str, user_id: uuid.UUID, data: dict[str, Any] | None = None
) -> None:
    """Publish a notification event to the shared Redis events:notify channel."""
    if data is None:
        data = {}
    event = NotifyEvent(
        type=event_type,
        user_id=user_id,
        data=data,
    )
    await redis_client.publish(EVENT_CHANNEL, event.model_dump_json())
