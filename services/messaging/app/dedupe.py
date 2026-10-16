from app.redis_client import redis

TTL_SECONDS = 86400  # Meta stops retrying long before 24h


def _key(msg_id: str) -> str:
    return f"wa:msg:{msg_id}"


async def is_duplicate(msg_id: str) -> bool:
    # NX = only set if missing: check-and-save in one atomic step
    was_new = await redis.set(_key(msg_id), "1", nx=True, ex=TTL_SECONDS)
    return not was_new


async def forget(msg_id: str) -> None:
    await redis.delete(_key(msg_id))
