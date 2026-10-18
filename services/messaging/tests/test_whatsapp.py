import logging

import httpx
import pytest
from pydantic import SecretStr

from app import whatsapp
from app.config import settings

TO = "8801700001234"


@pytest.fixture
def fake_mode(monkeypatch):
    monkeypatch.setattr(settings, "wa_fake_send", True)
    whatsapp.fake_outbox.clear()
    return whatsapp.fake_outbox


@pytest.fixture
def meta(monkeypatch):
    """Real mode against a fake Meta. Set meta["replies"] to the status codes to return."""
    state = {"replies": [200], "calls": []}

    def respond(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request)
        code = state["replies"].pop(0)
        if code == "timeout":
            raise httpx.ConnectTimeout("slow")
        if 400 <= code < 500:  # Meta's error body shape
            error = {"code": 131030, "message": "Recipient not in allowed list"}
            return httpx.Response(code, json={"error": error})
        return httpx.Response(code, json={})

    async def no_sleep(seconds):
        pass

    monkeypatch.setattr(settings, "wa_fake_send", False)
    monkeypatch.setattr(settings, "wa_access_token", SecretStr("secret-token-123"))
    monkeypatch.setattr(settings, "wa_phone_number_id", "555")
    monkeypatch.setattr(whatsapp, "transport", httpx.MockTransport(respond))
    monkeypatch.setattr(whatsapp.asyncio, "sleep", no_sleep)
    return state


# --- Fake mode (no token needed) ---


async def test_send_text_fake(fake_mode, caplog):
    caplog.set_level(logging.INFO)
    await whatsapp.send_text(TO, "Hi 👋")
    assert fake_mode == [
        {"messaging_product": "whatsapp", "to": TO, "type": "text", "text": {"body": "Hi 👋"}}
    ]
    out = caplog.text
    assert "****1234" in out
    assert TO not in out and "Hi 👋" not in out  # no full phone, no text in logs


async def test_send_buttons_fake(fake_mode):
    await whatsapp.send_buttons(TO, "Pick one", [("rates", "📈 Rates"), ("freeze", "🧊 Freeze")])
    interactive = fake_mode[0]["interactive"]
    assert interactive["type"] == "button"
    assert interactive["body"] == {"text": "Pick one"}
    assert interactive["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "rates", "title": "📈 Rates"}},
        {"type": "reply", "reply": {"id": "freeze", "title": "🧊 Freeze"}},
    ]


@pytest.mark.parametrize("count", [0, 4])
async def test_send_buttons_needs_1_to_3(fake_mode, count):
    with pytest.raises(ValueError):
        await whatsapp.send_buttons(TO, "x", [(str(i), "b") for i in range(count)])
    assert fake_mode == []


async def test_send_link_button_fake(fake_mode, caplog):
    caplog.set_level(logging.INFO)
    await whatsapp.send_link_button(TO, "Approve", "Review", "https://id.test/approve/abc")
    interactive = fake_mode[0]["interactive"]
    assert interactive["type"] == "cta_url"
    assert interactive["action"] == {
        "name": "cta_url",
        "parameters": {"display_text": "Review", "url": "https://id.test/approve/abc"},
    }
    assert "[fake send]" in caplog.text
    assert "approve/abc" not in caplog.text  # links can carry tokens


async def test_fake_send_url_forwards_to_the_mock_server(fake_mode, monkeypatch):
    # The worker and notifier are separate processes: WA_FAKE_SEND_URL lets a demo or the
    # e2e test see their messages in mock_server.py. Meta is never called.
    import mock_server

    mock_server.sent.clear()
    monkeypatch.setattr(settings, "wa_fake_send_url", "http://fake-wa/fake-whatsapp/send")
    monkeypatch.setattr(whatsapp, "transport", httpx.ASGITransport(app=mock_server.app))
    await whatsapp.send_text(TO, "Hi")
    assert mock_server.sent == fake_mode
    assert mock_server.sent[0]["text"] == {"body": "Hi"}


# --- Real mode (fake Meta) ---


async def test_real_send_uses_token_and_url(meta):
    await whatsapp.send_text(TO, "hi")
    [request] = meta["calls"]
    assert str(request.url) == "https://graph.facebook.com/v21.0/555/messages"
    assert request.headers["Authorization"] == "Bearer secret-token-123"


async def test_5xx_is_retried(meta):
    meta["replies"] = [500, 503, 200]
    await whatsapp.send_text(TO, "hi")
    assert len(meta["calls"]) == 3


async def test_timeout_is_retried(meta):
    meta["replies"] = ["timeout", 200]
    await whatsapp.send_text(TO, "hi")
    assert len(meta["calls"]) == 2


async def test_gives_up_after_3_tries(meta):
    meta["replies"] = [500, 500, 500]
    with pytest.raises(httpx.HTTPStatusError):
        await whatsapp.send_text(TO, "hi")
    assert len(meta["calls"]) == 3


async def test_4xx_is_never_retried(meta):
    meta["replies"] = [400]
    with pytest.raises(RuntimeError):
        await whatsapp.send_text(TO, "hi")
    assert len(meta["calls"]) == 1


async def test_4xx_error_shows_meta_code_and_message(meta):
    meta["replies"] = [400]
    with pytest.raises(RuntimeError) as error:
        await whatsapp.send_text(TO, "hi")
    text = str(error.value)
    assert "400" in text and "131030" in text and "Recipient not in allowed list" in text
    assert TO not in text  # no full phone in errors


async def test_missing_token_refuses_to_send(meta, monkeypatch):
    monkeypatch.setattr(settings, "wa_access_token", SecretStr(""))
    with pytest.raises(RuntimeError, match="WA_FAKE_SEND"):
        await whatsapp.send_text(TO, "hi")
    assert meta["calls"] == []


async def test_token_never_appears_in_errors(meta):
    meta["replies"] = [401]
    with pytest.raises(RuntimeError) as error:
        await whatsapp.send_text(TO, "hi")
    assert "secret-token-123" not in str(error.value)
