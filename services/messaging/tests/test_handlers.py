import uuid

import httpx
import pytest
from contracts.models import UserOut

from app import handlers, whatsapp
from app.clients import IdentityClient, ServiceUnavailable
from app.config import settings
from tests.mocks import mock_identity

PHONE = "8801700001234"


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    monkeypatch.setattr(settings, "wa_fake_send", True)
    whatsapp.fake_outbox.clear()
    mock_identity.frozen.clear()
    client = IdentityClient(transport=httpx.ASGITransport(app=mock_identity.app))
    monkeypatch.setattr(handlers, "identity", client)
    return client


@pytest.fixture
def new_user(monkeypatch):
    # The mock identity answers 404 (unknown user) for this hash
    monkeypatch.setattr(handlers, "hash_phone", lambda phone: mock_identity.UNKNOWN_HASH)


def job(text="hi", type_="text", button_id=""):
    return {
        "msg_id": f"wamid.{uuid.uuid4().hex}",
        "from": PHONE,
        "type": type_,
        "text": text,
        "button_id": button_id,
    }


def replies() -> list[str]:
    # The body text of every message we "sent" (fake send mode)
    return [
        m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]
        for m in whatsapp.fake_outbox
    ]


def user_id_of(phone=PHONE) -> uuid.UUID:
    return mock_identity.user_id_for(handlers.hash_phone(phone))


# --- Step 2.2: linking flow ---


async def test_new_user_gets_link_button(new_user):
    await handlers.handle_message(job())
    [message] = whatsapp.fake_outbox
    assert message["interactive"]["type"] == "cta_url"
    assert message["interactive"]["body"]["text"] == handlers.WELCOME
    assert message["interactive"]["action"]["parameters"]["url"] == "http://localhost:8003/link/demo"


async def test_known_but_unlinked_user_gets_link_button(monkeypatch, identity):
    async def unlinked(phone_hash):
        return UserOut(user_id=uuid.uuid4(), linked=False, frozen=False)

    monkeypatch.setattr(identity, "get_user_by_phone", unlinked)
    await handlers.handle_message(job())
    assert replies() == [handlers.WELCOME]


async def test_link_token_gets_the_hash_and_wa_id(new_user, monkeypatch, identity):
    seen = {}
    real = identity.create_link_token

    async def spy(phone_hash, wa_id):
        seen.update(phone_hash=phone_hash, wa_id=wa_id)
        return await real(phone_hash, wa_id)

    monkeypatch.setattr(identity, "create_link_token", spy)
    await handlers.handle_message(job())
    assert seen == {"phone_hash": mock_identity.UNKNOWN_HASH, "wa_id": PHONE}


async def test_frozen_user_gets_frozen_message():
    mock_identity.frozen.add(user_id_of())
    await handlers.handle_message(job())
    assert replies() == [handlers.FROZEN]


async def test_linked_user_is_passed_on():
    await handlers.handle_message(job())
    assert replies() == ["Linked ✅"]


async def test_identity_down_sends_sorry(monkeypatch, identity):
    async def down(phone_hash):
        raise ServiceUnavailable("identity unreachable")

    monkeypatch.setattr(identity, "get_user_by_phone", down)
    await handlers.handle_message(job())
    assert replies() == [handlers.SORRY]
