"""Calls to Engineer C's identity service."""

import httpx
from contracts.models import ApprovalCreate, ApprovalCreated, VerifySignatureIn, VerifySignatureOut

from app.config import settings

# Tests point this at the mock identity app; None = real network
transport: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.identity_url,
        headers={"X-Internal-Key": settings.internal_api_key, "Content-Type": "application/json"},
        timeout=10,
        transport=transport,
    )


async def create_approval(data: ApprovalCreate) -> ApprovalCreated:
    async with _client() as client:
        response = await client.post("/internal/approvals", content=data.model_dump_json())
        response.raise_for_status()
        return ApprovalCreated.model_validate(response.json())


async def verify_signature(data: VerifySignatureIn) -> bool:
    # Any error (C down, bad answer) counts as "not valid": fail closed
    try:
        async with _client() as client:
            response = await client.post(
                "/internal/verify-signature", content=data.model_dump_json()
            )
            response.raise_for_status()
            return VerifySignatureOut.model_validate(response.json()).valid is True
    except Exception:
        return False
