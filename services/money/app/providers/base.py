"""What every payment/FX provider must offer. The executor only talks to this interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ProviderStatus = Literal["pending", "settled", "failed", "unknown"]


class ProviderTimeout(Exception):
    """We don't know if the provider got the request. NEVER resubmit: ask status() instead."""


class ProviderRejected(Exception):
    """The provider definitely did not move the money."""


@dataclass(frozen=True)
class ProviderQuote:
    rate: Decimal
    fee: Decimal
    amount_out: Decimal


@dataclass(frozen=True)
class SubmitDetails:
    from_currency: str
    to_currency: str
    amount_in: Decimal
    amount_out: Decimal


class Provider(ABC):
    name: str

    @abstractmethod
    async def quote(self, pair: str, amount: Decimal) -> ProviderQuote: ...

    @abstractmethod
    async def submit(self, idempotency_key: str, details: SubmitDetails) -> str:
        """Returns provider_ref. Same key twice must return the same ref and move money once."""

    @abstractmethod
    async def status(self, idempotency_key: str) -> ProviderStatus: ...
