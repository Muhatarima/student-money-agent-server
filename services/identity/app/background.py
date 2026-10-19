"""Background loops inside the API process, for hosting plans that allow only a few services.

Turned on with RUN_JOBS_IN_API=true (off by default). The separate entrypoints still work;
never run both for the same service at once, or every job runs twice.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

RESTART_DELAY = 5  # seconds before a crashed job is started again


async def supervise(name: str, job: Callable[[], Awaitable[None]], delay: float = RESTART_DELAY):
    """Run job() forever: if it crashes (or returns), log it and start it again."""
    while True:
        try:
            await job()
            log.warning("%s stopped, restarting in %ss", name, delay)
        except Exception:
            log.exception("%s crashed, restarting in %ss", name, delay)
        await asyncio.sleep(delay)


@asynccontextmanager
async def running(jobs: dict[str, Callable[[], Awaitable[None]]]):
    """Start every job as a supervised task; stop them all when the API shuts down."""
    tasks = [asyncio.create_task(supervise(name, job), name=name) for name, job in jobs.items()]
    log.info("background jobs running in the api: %s", ", ".join(jobs))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
