import json
import logging

import pytest

from app import worker
from app.queue import DEAD, INCOMING, PROCESSING, enqueue_incoming


def job(msg_id="wamid.W1"):
    return {"msg_id": msg_id, "from": "8801700001234", "text": "hello"}


@pytest.fixture
def handled(monkeypatch):
    """Replace the slow handler; tests choose whether it fails."""
    calls = {"ids": [], "fail": False}

    async def fake_handle(j):
        calls["ids"].append(j["msg_id"])
        if calls["fail"]:
            raise RuntimeError("boom")

    monkeypatch.setattr(worker, "handle", fake_handle)
    return calls


async def take_one(redis) -> str:
    # What the worker loop does: move one job INCOMING -> PROCESSING
    return await redis.lmove(INCOMING, PROCESSING, src="RIGHT", dest="LEFT")


async def test_success_removes_job_from_processing(redis, handled):
    await enqueue_incoming(job())
    await worker.process(await take_one(redis))
    assert handled["ids"] == ["wamid.W1"]
    assert await redis.llen(PROCESSING) == 0
    assert await redis.llen(INCOMING) == 0


async def test_job_survives_a_crash(redis, handled):
    await enqueue_incoming(job())
    await take_one(redis)  # worker took it... and crashed before finishing
    assert await redis.llen(PROCESSING) == 1

    assert await worker.recover() == 1  # worker restarts
    assert await redis.llen(PROCESSING) == 0
    assert json.loads(await redis.lindex(INCOMING, 0)) == job()


async def test_recovery_keeps_the_order(redis):
    for i in range(3):
        await enqueue_incoming(job(f"wamid.{i}"))
    for _ in range(3):
        await take_one(redis)
    await worker.recover()
    order = [json.loads(await take_one(redis))["msg_id"] for _ in range(3)]
    assert order == ["wamid.0", "wamid.1", "wamid.2"]


async def test_recover_with_nothing_to_do(redis):
    assert await worker.recover() == 0


async def test_failure_is_retried(redis, handled):
    handled["fail"] = True
    await enqueue_incoming(job())
    await worker.process(await take_one(redis))
    assert await redis.llen(INCOMING) == 1  # back in line for another try
    assert await redis.llen(PROCESSING) == 0
    assert await redis.get("queue:failures:wamid.W1") == "1"


async def test_three_failures_go_to_dead_queue(redis, handled):
    handled["fail"] = True
    await enqueue_incoming(job())
    for _ in range(3):
        await worker.process(await take_one(redis))

    assert handled["ids"] == ["wamid.W1"] * 3
    assert await redis.llen(DEAD) == 1
    assert await redis.llen(INCOMING) == 0
    assert await redis.llen(PROCESSING) == 0


async def test_success_after_a_failure_clears_the_counter(redis, handled):
    handled["fail"] = True
    await enqueue_incoming(job())
    await worker.process(await take_one(redis))
    handled["fail"] = False
    await worker.process(await take_one(redis))
    assert await redis.exists("queue:failures:wamid.W1") == 0
    assert await redis.llen(DEAD) == 0


async def test_unreadable_job_goes_to_dead_queue(redis, handled):
    await redis.lpush(INCOMING, "not json")
    await worker.process(await take_one(redis))
    assert await redis.lrange(DEAD, 0, -1) == ["not json"]
    assert handled["ids"] == []


async def test_handler_never_logs_message_text(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    async def fake_handle_message(job):
        pass

    monkeypatch.setattr(worker.handlers, "handle_message", fake_handle_message)
    await worker.handle({"msg_id": "wamid.X", "from": "8801700001234", "text": "secret text"})
    out = caplog.text
    assert "secret text" not in out
    assert "8801700001234" not in out and "****1234" in out
