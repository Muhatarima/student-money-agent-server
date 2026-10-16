"""HTTP clients for Money (B) and Identity (C). Shapes come from packages/contracts."""

import uuid

import httpx
from contracts.models import (
    BalanceOut,
    LinkTokenOut,
    ProposalResult,
    RateOut,
    RuleProposal,
    TransferProposal,
    UserOut,
)

from app.config import settings


class ServiceUnavailable(Exception):
    """The other service can't be reached or is broken. Callers show a friendly message."""


class _InternalClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.transport = transport  # tests pass a mock app here

    async def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        headers = {"X-Internal-Key": settings.internal_api_key.get_secret_value()}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, headers=headers, timeout=10, transport=self.transport
            ) as client:
                response = await client.request(method, path, json=json)
        except httpx.TransportError as e:
            raise ServiceUnavailable(f"{self.base_url} unreachable") from e
        if response.status_code >= 500:
            raise ServiceUnavailable(f"{self.base_url} answered {response.status_code}")
        return response


class IdentityClient(_InternalClient):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings.identity_url, transport)

    async def get_user_by_phone(self, phone_hash: str) -> UserOut | None:
        response = await self._request("GET", f"/internal/users/by-phone/{phone_hash}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return UserOut.model_validate(response.json())

    async def create_link_token(self, phone_hash: str, wa_id: str) -> LinkTokenOut:
        body = {"phone_hash": phone_hash, "wa_id": wa_id}
        response = await self._request("POST", "/internal/link-tokens", json=body)
        response.raise_for_status()
        return LinkTokenOut.model_validate(response.json())

    async def freeze(self, user_id: uuid.UUID) -> None:
        response = await self._request("POST", f"/internal/users/{user_id}/freeze")
        response.raise_for_status()

    async def get_wa_target(self, user_id: uuid.UUID) -> str:
        response = await self._request("GET", f"/internal/users/{user_id}/wa-target")
        response.raise_for_status()
        return response.json()["wa_id"]

    async def create_upload_link(self, user_id: uuid.UUID) -> str:
        body = {"user_id": str(user_id)}
        response = await self._request("POST", "/internal/upload-links", json=body)
        response.raise_for_status()
        return response.json()["url"]


class MoneyClient(_InternalClient):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings.money_url, transport)

    async def get_rate(self, pair: str) -> RateOut:
        response = await self._request("GET", f"/internal/rates/{pair}")
        response.raise_for_status()
        return RateOut.model_validate(response.json())

    async def get_balance(self, user_id: uuid.UUID) -> BalanceOut:
        response = await self._request("GET", f"/internal/balance/{user_id}")
        response.raise_for_status()
        return BalanceOut.model_validate(response.json())

    async def propose_transfer(self, proposal: TransferProposal) -> ProposalResult:
        body = proposal.model_dump(mode="json")
        response = await self._request("POST", "/internal/proposals/transfer", json=body)
        response.raise_for_status()
        return ProposalResult.model_validate(response.json())

    async def propose_rule(self, proposal: RuleProposal) -> ProposalResult:
        body = proposal.model_dump(mode="json")
        response = await self._request("POST", "/internal/proposals/rule", json=body)
        response.raise_for_status()
        return ProposalResult.model_validate(response.json())

    async def list_rules(self, user_id: uuid.UUID) -> list[dict]:
        response = await self._request("GET", f"/internal/rules/{user_id}")
        response.raise_for_status()
        return response.json()

    async def freeze_money(self, user_id: uuid.UUID) -> None:
        response = await self._request("POST", f"/internal/users/{user_id}/freeze-money")
        response.raise_for_status()
