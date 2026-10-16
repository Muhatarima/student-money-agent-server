"""Decides what to do with each incoming WhatsApp message (called by the worker).

Safety-critical commands (FREEZE, menu buttons) never go through the LLM.
"""

import logging

from app import agent, whatsapp
from app.clients import ServiceUnavailable
from app.security import hash_phone, mask_phone

log = logging.getLogger(__name__)

WELCOME = "Welcome! Tap to secure your account with your fingerprint."
FROZEN = "Your account is frozen. Reply UNFREEZE help to learn how to unlock."
SORRY = "Sorry, something went wrong on our side. Please try again in a minute."
FREEZE_DONE = "🧊 Done. Everything is frozen. No money can move."
MENU_TEXT = "What would you like to do?"
MENU = [("rates", "📈 Rates"), ("rules", "⚙️ My rules"), ("freeze", "🧊 Freeze")]


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
    user = await agent.identity.get_user_by_phone(phone_hash)

    if user is None or not user.linked:
        link = await agent.identity.create_link_token(phone_hash, wa_id=sender)
        await whatsapp.send_link_button(sender, WELCOME, "Secure my account", link.url)
        return
    if user.frozen:
        await whatsapp.send_text(sender, FROZEN)
        return

    # Button taps and the FREEZE word: plain code, no LLM
    command = job["button_id"] or job["text"].strip().lower()
    if command == "freeze":
        await agent.freeze_everything(user.user_id)
        await whatsapp.send_text(sender, FREEZE_DONE)
    elif command == "rates" and job["button_id"]:
        rate = await agent.money.get_rate("BDT_IDR")
        await whatsapp.send_text(sender, f"📈 1 BDT = {rate.rate} IDR")
    elif command == "rules" and job["button_id"]:
        await whatsapp.send_text(sender, await _rules_text(user.user_id))
    elif job["button_id"]:
        await _send_menu(sender)  # a button we don't know (e.g. an old message)
    else:
        reply = await agent.run_agent(user.user_id, job["text"], job["msg_id"])
        if reply is None:  # AI unavailable: the menu still works without it
            await _send_menu(sender)
        else:
            await whatsapp.send_text(sender, reply)


async def _rules_text(user_id) -> str:
    rules = await agent.money.list_rules(user_id)
    if not rules:
        return "You have no auto-rules yet."
    lines = [f"• {r['pair']} at {r['target_rate']} ({r['status']})" for r in rules]
    return "⚙️ Your auto-rules:\n" + "\n".join(lines)


async def _send_menu(sender: str) -> None:
    await whatsapp.send_buttons(sender, MENU_TEXT, MENU)
