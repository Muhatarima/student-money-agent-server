"""Fake provider for the MVP. No real money ever moves through it."""

import random
import uuid
from decimal import ROUND_DOWN, Decimal

from app.config import settings
from app.providers.base import (
    Provider,
    ProviderQuote,
    ProviderRejected,
    ProviderStatus,
    ProviderTimeout,
    SubmitDetails,
)

# Fixed rates until the fx module (Day 3) supplies live ones
MOCK_RATES = {"BDT_IDR": Decimal("135.20")}


class MockProvider(Provider):
    name = "mock"

    def __init__(
        self,
        failure_rate: float | None = None,
        timeout_rate: float | None = None,
        rng: random.Random | None = None,
    ):
        self.failure_rate = settings.mock_failure_rate if failure_rate is None else failure_rate
        self.timeout_rate = settings.mock_timeout_rate if timeout_rate is None else timeout_rate
        self.rng = rng or random.Random()
        # idempotency_key -> (provider_ref, final status)
        self.records: dict[str, tuple[str, ProviderStatus]] = {}
        # How many times money was "really moved" per key. Must never go above 1.
        self.executions: dict[str, int] = {}
        self.submit_calls: list[str] = []

    async def quote(self, pair: str, amount: Decimal) -> ProviderQuote:
        if pair not in MOCK_RATES:
            raise ProviderRejected(f"unsupported pair {pair}")
        rate = MOCK_RATES[pair]
        amount_out = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return ProviderQuote(rate=rate, fee=Decimal("0"), amount_out=amount_out)

    async def submit(self, idempotency_key: str, details: SubmitDetails) -> str:
        self.submit_calls.append(idempotency_key)
        if idempotency_key in self.records:
            # Seen before: same answer, nothing executes again
            return self.records[idempotency_key][0]

        ref = f"mock-{uuid.uuid4().hex[:12]}"
        if self.rng.random() < self.failure_rate:
            self.records[idempotency_key] = (ref, "failed")
            raise ProviderRejected("mock provider declined")

        self.records[idempotency_key] = (ref, "settled")
        self.executions[idempotency_key] = self.executions.get(idempotency_key, 0) + 1
        if self.rng.random() < self.timeout_rate:
            # The money moved, but our caller never hears back
            raise ProviderTimeout("mock provider timed out")
        return ref

    async def status(self, idempotency_key: str) -> ProviderStatus:
        record = self.records.get(idempotency_key)
        return record[1] if record else "unknown"

    def ref_for(self, idempotency_key: str) -> str | None:
        record = self.records.get(idempotency_key)
        return record[0] if record else None
