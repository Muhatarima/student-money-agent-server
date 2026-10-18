"""Helpers for the e2e demo: a fake student with a phone (WhatsApp) and a browser (passkey)."""

import hashlib
import hmac
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from passkey import SoftPasskey

# Must match docker-compose.yml (test-only values)
COMPOSE_FILE = str(Path(__file__).with_name("docker-compose.yml"))
COMPOSE = ["docker", "compose", "-p", "sma-e2e", "-f", COMPOSE_FILE]
MESSAGING = "http://localhost:18001"
MONEY = "http://localhost:18002"
IDENTITY = "http://localhost:18003"
FAKE_WHATSAPP = "http://localhost:18009"
INTERNAL_KEY = "e2e-internal-key"
APP_SECRET = b"e2e-app-secret"
PHONE_HASH_KEY = b"e2e-phone-hash-key"

# Generous timeouts: Docker Desktop on Windows sometimes pauses for many seconds
TIMEOUT = 30
INTERNAL = {"X-Internal-Key": INTERNAL_KEY}
money = httpx.Client(base_url=MONEY, headers=INTERNAL, timeout=TIMEOUT)
identity = httpx.Client(base_url=IDENTITY, headers=INTERNAL, timeout=TIMEOUT)
whatsapp = httpx.Client(timeout=TIMEOUT)


def compose(*args: str) -> str:
    result = subprocess.run(
        [*COMPOSE, *args], capture_output=True, encoding="utf-8", errors="replace", check=True
    )
    return result.stdout


def run_in(service: str, *command: str) -> str:
    return compose("exec", "-T", service, *command)


def text_of(message: dict) -> str:
    """What the student sees: the text, plus the link of a link button."""
    if message["type"] == "text":
        return message["text"]["body"]
    interactive = message["interactive"]
    text = interactive["body"]["text"]
    if interactive["type"] == "cta_url":
        text += " " + interactive["action"]["parameters"]["url"]
    return text


def link_in(message: dict) -> str:
    return message["interactive"]["action"]["parameters"]["url"]


class Student:
    def __init__(self, phone: str):
        self.phone = phone
        self.seen = 0  # messages to this student we have already looked at
        self.passkey = SoftPasskey(origin=IDENTITY, rp_id="localhost")
        # The "browser": keeps cookies, and sends our own site as Origin like a real page
        self.browser = httpx.Client(headers={"Origin": IDENTITY}, timeout=TIMEOUT)

    @property
    def user_id(self) -> str:
        digits = re.sub(r"\D", "", self.phone).encode()
        phone_hash = hmac.new(PHONE_HASH_KEY, digits, hashlib.sha256).hexdigest()
        response = identity.get(f"/internal/users/by-phone/{phone_hash}")
        response.raise_for_status()
        return response.json()["user_id"]

    # --- WhatsApp ---

    def say(self, text: str) -> None:
        """Send a WhatsApp message, signed like Meta signs it."""
        message = {
            "from": self.phone,
            "id": f"wamid.e2e.{uuid.uuid4().hex}",
            "timestamp": str(int(time.time())),
            "type": "text",
            "text": {"body": text},
        }
        body = json.dumps({"entry": [{"changes": [{"value": {"messages": [message]}}]}]}).encode()
        signature = "sha256=" + hmac.new(APP_SECRET, body, hashlib.sha256).hexdigest()
        response = whatsapp.post(
            f"{MESSAGING}/webhook",
            content=body,
            headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
        )
        assert response.json()["new"] == 1

    def new_messages(self) -> list[dict]:
        sent = whatsapp.get(f"{FAKE_WHATSAPP}/fake-whatsapp/sent").json()
        mine = [m for m in sent if m["to"] == "****" + self.phone[-4:]]  # phones come masked
        return mine[self.seen :]

    def wait_for(self, part: str, timeout: float = 30) -> dict:
        """The first new message containing `part`. Messages before it count as seen."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                messages = self.new_messages()
            except httpx.TimeoutException:
                continue  # Docker paused for a moment: just ask again
            for i, message in enumerate(messages):
                if part in text_of(message):
                    self.seen += i + 1
                    return message
            time.sleep(0.5)
        texts = [text_of(m) for m in self.new_messages()]
        raise AssertionError(f"no message with {part!r} within {timeout}s; got {texts}")

    # --- Browser pages ---

    def register_passkey(self, link_url: str) -> None:
        token = _token(link_url)
        assert self.browser.get(link_url).status_code == 200
        options = self._post("/passkeys/register/options", {"t": token})
        credential = self.passkey.create(options)
        result = self._post("/passkeys/register/verify", {"t": token, "credential": credential})
        assert result["ok"] is True

    def approve(self, approval_url: str) -> str:
        """Open the approval page, touch the 'fingerprint'. Returns the page the user saw."""
        page = self.browser.get(approval_url)
        assert page.status_code == 200, page.text
        path, token = urlparse(approval_url).path, _token(approval_url)
        options = self._post(f"{path}/options", {"t": token})
        credential = self.passkey.get(options)
        result = self._post(f"{path}/verify", {"t": token, "credential": credential})
        assert result == {"ok": True, "redirect": "/approved"}
        return page.text

    def upload(self, upload_url: str, data: bytes) -> str:
        """Passkey login on the upload page, then upload. Returns the result page."""
        path, token = urlparse(upload_url).path, _token(upload_url)
        options = self._post(f"{path}/options", {"t": token})
        self._post(f"{path}/verify", {"t": token, "credential": self.passkey.get(options)})
        page = self.browser.get(upload_url).text  # now logged in: the form has a CSRF token
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        response = self.browser.post(
            IDENTITY + path,
            data={"t": token, "csrf": csrf, "doc_type": "passport"},
            files={"file": ("passport.pdf", data, "application/pdf")},
        )
        assert response.status_code == 200, response.text
        return response.text

    def _post(self, path: str, body: dict) -> dict:
        response = self.browser.post(IDENTITY + path, json=body)
        assert response.status_code == 200, response.text
        return response.json()


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["t"][0]
