"""Background jobs inside the API (RUN_JOBS_IN_API): restarted after a crash, stopped at
shutdown, and off unless the setting is on."""

import asyncio

from app import background, main
from app.config import settings


async def test_a_crashed_job_is_restarted():
    calls = []
    restarted = asyncio.Event()

    async def job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        restarted.set()
        await asyncio.Event().wait()  # keeps running

    task = asyncio.create_task(background.supervise("test-job", job, delay=0))
    await asyncio.wait_for(restarted.wait(), timeout=2)
    task.cancel()
    assert len(calls) == 2


async def test_a_job_that_returns_is_started_again():
    calls = []
    twice = asyncio.Event()

    async def job():
        calls.append(1)
        if len(calls) == 2:
            twice.set()

    task = asyncio.create_task(background.supervise("test-job", job, delay=0))
    await asyncio.wait_for(twice.wait(), timeout=2)
    task.cancel()


async def test_jobs_stop_when_the_api_shuts_down():
    started, stopped = asyncio.Event(), asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with background.running({"test-job": job}):
        await asyncio.wait_for(started.wait(), timeout=2)
    assert stopped.is_set()


def fake_jobs(monkeypatch, module, *names):
    """Replace module.<name> functions with fakes that record that they ran."""
    ran = {name: asyncio.Event() for name in names}

    for name in names:

        async def fake(*args, _name=name):
            ran[_name].set()
            await asyncio.Event().wait()

        monkeypatch.setattr(module, name, fake)
    return ran


async def test_jobs_are_off_by_default(monkeypatch):
    assert settings.run_jobs_in_api is False
    started = []
    monkeypatch.setattr(background, "running", lambda jobs: started.append(jobs))
    async with main.lifespan(main.app):
        pass
    assert started == []


async def test_identity_api_runs_the_purge_loop(monkeypatch):
    monkeypatch.setattr(settings, "run_jobs_in_api", True)
    ran = fake_jobs(monkeypatch, main.jobs, "main")
    async with main.lifespan(main.app):
        await asyncio.wait_for(ran["main"].wait(), timeout=2)
