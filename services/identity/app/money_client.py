"""Calls to Engineer B's money service."""

import httpx
from contracts.models import ApprovalConfirmed

from app.config import settings

# Tests point this at a mock money app; None = real network
transport: httpx.AsyncBaseTransport | None = None


async def confirm_approval(data: ApprovalConfirmed) -> bool:
    """Tell Money the user approved. False if Money refused or can't be reached."""
    headers = {
        "X-Internal-Key": settings.internal_api_key.get_secret_value(),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.money_url, headers=headers, timeout=10, transport=transport
        ) as client:
            response = await client.post(
                "/internal/approvals/confirmed", content=data.model_dump_json()
            )
    except httpx.HTTPError:
        return False
    return response.is_success
