"""Starts the whole system in Docker before the e2e tests and removes it afterwards.

Run (Docker Desktop must be running), starting in the repo root:
    cd tests\\e2e
    python -m venv .venv
    .venv\\Scripts\\Activate.ps1
    pip install -r requirements.txt
    pytest -q
Set E2E_KEEP=1 to leave the containers running afterwards (to read their logs:
docker compose -p sma-e2e logs).
"""

import os

import pytest

from demo import compose


@pytest.fixture(scope="session", autouse=True)
def stack():
    compose("down", "-v")  # start from empty databases every time
    compose("up", "-d", "--build", "--wait")
    yield
    if os.environ.get("E2E_KEEP") != "1":
        compose("down", "-v")
