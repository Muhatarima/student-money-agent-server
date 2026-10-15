"""Background loops. Run with: python -m app.jobs"""

import asyncio
import logging

from app.fx import fetch_rates
from app.redis_client import redis

log = logging.getLogger(__name__)


async def rates_loop() -> None:
    while True:
        try:
            await fetch_rates(redis)
        except Exception:
            log.exception("fetch_rates failed")  # keep running; stale rates block money anyway
        await asyncio.sleep(60)


async def main() -> None:
    await rates_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
