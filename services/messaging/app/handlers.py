"""Decides what to do with each incoming WhatsApp message (called by the worker)."""

import logging

from app import whatsapp
from app.clients import IdentityClient, ServiceUnavailable
from app.security import hash_phone, mask_phone

log = logging.getLogger(__name__)

identity = IdentityClient()

WELCOME = "Welcome! Tap to secure your account with your fingerprint."
FROZEN = "Your account is frozen. Reply UNFREEZE help to learn how to unlock."
SORRY = "Sorry, something went wrong on our side. Please try again in a minute."


async def handle_message(job: dict) -> None:
    sender = job["from"]
    try:
        await _handle(job, sender)
    except ServiceUnavailable:
        # Money or Identity is down: tell the user instead of retrying in silence
        log.warning("service unavailable while handling a message from %s", mask_phone(sender))
        await whatsapp.send_text(sender, SORRY)


async def _handle(job: dict, sender: str) -> None:
    phone_hash = hash_phone(sender)
    user = await identity.get_user_by_phone(phone_hash)

    if user is None or not user.linked:
        link = await identity.create_link_token(phone_hash, wa_id=sender)
        await whatsapp.send_link_button(sender, WELCOME, "Secure my account", link.url)
        return
    if user.frozen:
        await whatsapp.send_text(sender, FROZEN)
        return

    await whatsapp.send_text(sender, "Linked ✅")  # the AI agent comes here (step 3.2)
