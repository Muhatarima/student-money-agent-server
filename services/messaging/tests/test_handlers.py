import uuid
from types import SimpleNamespace

import httpx
import pytest
from contracts.models import UserOut

from app import agent, handlers, whatsapp
from app.clients import IdentityClient, MoneyClient, ServiceUnavailable
from app.config import settings
from tests.mocks import mock_identity, mock_money

PHONE = "8801700001234"


@pytest.fixture(autouse=True)
def clients(monkeypatch):
    monkeypatch.setattr(settings, "wa_fake_send", True)
    whatsapp.fake_outbox.clear()
    mock_identity.frozen.clear()
    identity = IdentityClient(transport=httpx.ASGITransport(app=mock_identity.app))
    money = MoneyClient(transport=httpx.ASGITransport(app=mock_money.app))
    monkeypatch.setattr(agent, "identity", identity)
    monkeypatch.setattr(agent, "money", money)
    return SimpleNamespace(identity=identity, money=money)


@pytest.fixture
def new_user(monkeypatch):
    # The mock identity answers 404 (unknown user) for this hash
    monkeypatch.setattr(handlers, "hash_phone", lambda phone: mock_identity.UNKNOWN_HASH)


@pytest.fixture
def llm(monkeypatch):
    """Replace the agent: tests choose its answer (None = AI unavailable)."""
    state = {"reply": "AI reply", "calls": []}

    async def fake_run_agent(user_id, text, msg_id):
        state["calls"].append(text)
        return state["reply"]

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    return state


def job(text="hi", type_="text", button_id=""):
    return {
        "msg_id": f"wamid.{uuid.uuid4().hex}",
        "from": PHONE,
        "type": type_,
        "text": text,
        "button_id": button_id,
    }


def button(button_id):
    return job(text="", type_="interactive", button_id=button_id)


def replies() -> list[str]:
    # The body text of every message we "sent" (fake send mode)
    return [
        m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]
        for m in whatsapp.fake_outbox
    ]


def user_id_of(phone=PHONE) -> uuid.UUID:
    return mock_identity.user_id_for(handlers.hash_phone(phone))


def sent_menu() -> bool:
    last = whatsapp.fake_outbox[-1]
    return last["type"] == "interactive" and last["interactive"]["type"] == "button"


# --- Step 2.2: linking flow ---


async def test_new_user_gets_link_button(new_user, llm):
    await handlers.handle_message(job())
    [message] = whatsapp.fake_outbox
    assert message["interactive"]["type"] == "cta_url"
    assert message["interactive"]["body"]["text"] == handlers.WELCOME
    assert message["interactive"]["action"]["parameters"]["url"] == "http://localhost:8003/link/demo"
    assert llm["calls"] == []  # unlinked users never reach the AI


async def test_known_but_unlinked_user_gets_link_button(monkeypatch, clients, llm):
    async def unlinked(phone_hash):
        return UserOut(user_id=uuid.uuid4(), linked=False, frozen=False)

    monkeypatch.setattr(clients.identity, "get_user_by_phone", unlinked)
    await handlers.handle_message(job())
    assert replies() == [handlers.WELCOME]


async def test_link_token_gets_the_hash_and_wa_id(new_user, monkeypatch, clients):
    seen = {}
    real = clients.identity.create_link_token

    async def spy(phone_hash, wa_id):
        seen.update(phone_hash=phone_hash, wa_id=wa_id)
        return await real(phone_hash, wa_id)

    monkeypatch.setattr(clients.identity, "create_link_token", spy)
    await handlers.handle_message(job())
    assert seen == {"phone_hash": mock_identity.UNKNOWN_HASH, "wa_id": PHONE}


async def test_frozen_user_gets_frozen_message(llm):
    mock_identity.frozen.add(user_id_of())
    await handlers.handle_message(job())
    assert replies() == [handlers.FROZEN]
    assert llm["calls"] == []


async def test_identity_down_sends_sorry(monkeypatch, clients):
    async def down(phone_hash):
        raise ServiceUnavailable("identity unreachable")

    monkeypatch.setattr(clients.identity, "get_user_by_phone", down)
    await handlers.handle_message(job())
    assert replies() == [handlers.SORRY]


# --- Step 3.2 / 3.3: agent, FREEZE, menu ---


async def test_linked_user_text_goes_to_the_agent(llm):
    await handlers.handle_message(job("what's the rate?"))
    assert llm["calls"] == ["what's the rate?"]
    assert replies() == ["AI reply"]


async def test_ai_unavailable_shows_the_menu(llm):
    llm["reply"] = None
    await handlers.handle_message(job("hello"))
    assert sent_menu()
    assert whatsapp.fake_outbox[-1]["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": i, "title": t}} for i, t in handlers.MENU
    ]


@pytest.mark.parametrize("word", ["FREEZE", "freeze", " Freeze "])
async def test_freeze_word_freezes_without_the_llm(llm, word):
    await handlers.handle_message(job(word))
    assert llm["calls"] == []
    assert replies() == [handlers.FREEZE_DONE]
    assert user_id_of() in mock_identity.frozen  # identity freeze was called


async def test_freeze_calls_both_services(monkeypatch, llm):
    called = []

    async def fake_freeze(user_id):
        called.append(user_id)

    monkeypatch.setattr(agent, "freeze_everything", fake_freeze)
    await handlers.handle_message(job("FREEZE"))
    assert called == [user_id_of()]


async def test_freeze_button(llm):
    await handlers.handle_message(button("freeze"))
    assert llm["calls"] == []
    assert replies() == [handlers.FREEZE_DONE]


async def test_rates_button_without_the_llm(llm):
    await handlers.handle_message(button("rates"))
    assert llm["calls"] == []
    assert replies() == ["📈 1 BDT = 135.20 IDR"]


async def test_rules_button_without_the_llm(llm):
    await handlers.handle_message(button("rules"))
    assert llm["calls"] == []
    assert replies() == ["⚙️ Your auto-rules:\n• BDT_IDR at 136 (active)"]


async def test_no_rules(monkeypatch, clients, llm):
    async def none(user_id):
        return []

    monkeypatch.setattr(clients.money, "list_rules", none)
    await handlers.handle_message(button("rules"))
    assert replies() == ["You have no auto-rules yet."]


async def test_unknown_button_shows_the_menu(llm):
    await handlers.handle_message(button("something_old"))
    assert llm["calls"] == [] and sent_menu()


async def test_typing_rates_goes_to_the_agent(llm):
    # Only the BUTTON skips the AI; the word "rates" is a normal question
    await handlers.handle_message(job("rates"))
    assert llm["calls"] == ["rates"]


# --- Day 4: rate limit, size limit, files only via the secure link ---


async def test_rate_limit_one_polite_reply_then_silence(llm):
    for i in range(handlers.MAX_PER_MINUTE + 5):
        await handlers.handle_message(job(f"message {i}"))
    assert len(llm["calls"]) == handlers.MAX_PER_MINUTE
    assert replies().count(handlers.TOO_FAST) == 1
    assert len(whatsapp.fake_outbox) == handlers.MAX_PER_MINUTE + 1


async def test_rate_limit_is_per_user(llm):
    for _ in range(handlers.MAX_PER_MINUTE):
        await handlers.handle_message(job())
    other = job() | {"from": "8801700009999"}
    await handlers.handle_message(other)
    assert len(llm["calls"]) == handlers.MAX_PER_MINUTE + 1


async def test_rate_limit_key_expires(llm, redis):
    await handlers.handle_message(job())
    [key] = await redis.keys("wa:rate:*")
    assert 0 < await redis.ttl(key) <= 60


async def test_too_long_text_is_refused(llm):
    await handlers.handle_message(job("x" * (handlers.MAX_TEXT_CHARS + 1)))
    assert llm["calls"] == []
    assert replies() == [handlers.TOO_LONG]


async def test_text_at_the_limit_is_fine(llm):
    await handlers.handle_message(job("x" * handlers.MAX_TEXT_CHARS))
    assert len(llm["calls"]) == 1


@pytest.mark.parametrize("type_", ["image", "document", "audio", "video", "sticker"])
async def test_files_get_the_secure_upload_link(llm, type_):
    await handlers.handle_message(job(text="", type_=type_))
    assert llm["calls"] == []
    [message] = whatsapp.fake_outbox
    assert message["interactive"]["body"]["text"] == handlers.UPLOAD_ONLY
    url = message["interactive"]["action"]["parameters"]["url"]
    assert url == "http://localhost:8003/upload/demo"


@pytest.mark.parametrize("word", ["UPLOAD", "upload"])
async def test_upload_word_gets_the_secure_link(llm, word):
    await handlers.handle_message(job(word))
    assert llm["calls"] == []
    assert replies() == [handlers.UPLOAD_ONLY]


async def test_unlinked_user_sending_a_file_gets_the_welcome_link(new_user, llm):
    await handlers.handle_message(job(text="", type_="image"))
    assert replies() == [handlers.WELCOME]
