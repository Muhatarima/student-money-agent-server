"""Smoke test: health endpoint returns 200 and {"status": "ok"}."""

import pytest


@pytest.mark.anyio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
