"""Tiny helpers that put rows in the test DB."""

import hashlib
import uuid

from app.crypto import encrypt_str
from app.models import User

WA_ID = "8801700001234"


def phone_hash(seed: str | None = None) -> str:
    return hashlib.sha256((seed or str(uuid.uuid4())).encode()).hexdigest()


async def make_user(session, linked=True, frozen=False, wa_id=WA_ID) -> User:
    user = User(
        phone_hash=phone_hash(), wa_id_encrypted=encrypt_str(wa_id), linked=linked, frozen=frozen
    )
    session.add(user)
    await session.flush()
    return user
