"""Send messages with the WhatsApp Cloud API. Never log the token, full phone or text."""

import asyncio
import logging

import httpx

from app.config import settings
from app.security import mask_phone

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Tests point this at a fake Meta; None = real network
transport: httpx.AsyncBaseTransport | None = None
# In fake mode, what would have been sent (handy in tests and demos)
fake_outbox: list[dict] = []


async def send_text(to: str, text: str) -> None:
    await _send(to, {"type": "text", "text": {"body": text}})


async def send_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> None:
    """buttons = [(id, title), ...]; WhatsApp allows 1 to 3."""
    if not 1 <= len(buttons) <= 3:
        raise ValueError("WhatsApp allows 1 to 3 buttons")
    replies = [{"type": "reply", "reply": {"id": id_, "title": title}} for id_, title in buttons]
    interactive = {"type": "button", "body": {"text": body}, "action": {"buttons": replies}}
    await _send(to, {"type": "interactive", "interactive": interactive})


async def send_link_button(to: str, body: str, button_text: str, url: str) -> None:
    action = {"name": "cta_url", "parameters": {"display_text": button_text, "url": url}}
    interactive = {"type": "cta_url", "body": {"text": body}, "action": action}
    await _send(to, {"type": "interactive", "interactive": interactive})


async def _send(to: str, message: dict) -> None:
    payload = {"messaging_product": "whatsapp", "to": to} | message
    if settings.wa_fake_send:
        fake_outbox.append(payload)
        if settings.wa_fake_send_url:  # lets demos and the e2e test see other processes' sends
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                response = await client.post(settings.wa_fake_send_url, json=payload)
                response.raise_for_status()
        log.info("[fake send] %s to %s", message["type"], mask_phone(to))
        return

    token = settings.wa_access_token.get_secret_value()
    if not token or not settings.wa_phone_number_id:
        raise RuntimeError(
            "WA_ACCESS_TOKEN / WA_PHONE_NUMBER_ID not set (or use WA_FAKE_SEND=true)"
        )

    url = (
        f"https://graph.facebook.com/{settings.wa_api_version}/"
        f"{settings.wa_phone_number_id}/messages"
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code < 500:
                    if response.is_error:  # 4xx = our mistake, retrying won't help
                        error = response.json().get("error", {})
                        code, reason = error.get("code"), error.get("message")
                        status = response.status_code
                        raise RuntimeError(f"Meta refused ({status}): {code} {reason}")
                    return
            except httpx.TransportError:  # timeouts and connection errors
                if attempt == MAX_ATTEMPTS:
                    raise
            else:
                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()  # still 5xx after the last try
            await asyncio.sleep(attempt)  # 1s, then 2s
