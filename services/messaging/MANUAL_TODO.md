# Things only you can do

Everything below has a fake/mock mode, so the code runs and the tests pass without it.

## 1. Create the local `.env` file (needed before running the app)

**What to do**
1. Open PowerShell in `services\messaging`.
2. `copy .env.example .env`
3. Fill in `WA_VERIFY_TOKEN` with any long random string you invent. Make one with:
   `python -c "import secrets; print(secrets.token_hex(32))"`
4. Set `REDIS_URL=redis://localhost:6379/0` (local Docker Redis), or your Upstash `rediss://` URL.
5. Leave `WA_FAKE_SEND=true` until step 2 below is done.

**Why** The app reads its secrets from `.env` and crashes at startup (naming the missing
field) if a required one is missing. `.env` is never committed.

**Affects** `services/messaging/.env`, read by `app/config.py`.

**How to check** `.venv\Scripts\python -c "from app.config import settings; print('ok')"` prints `ok`.

## 2. Real WhatsApp sending (Meta app + token)

**What to do**
1. Go to developers.facebook.com → My Apps → Create App → Business.
2. Add the **WhatsApp** product. Copy the **Phone Number ID** and the **temporary access token**.
3. Add your own phone as a test recipient.
4. App Settings → Basic → **App Secret**: copy it.
5. In `services\messaging\.env` set:
   - `WA_APP_SECRET=<App Secret>`
   - `WA_ACCESS_TOKEN=<access token>`
   - `WA_PHONE_NUMBER_ID=<Phone Number ID>`
   - `WA_FAKE_SEND=false`
6. Never paste these values into chat, code, or git.

**Why** `app/whatsapp.py` needs a real token to send replies. Without it, fake mode
records messages in `whatsapp.fake_outbox` and prints `[fake send] text to ****1234`.
In real mode with no token it refuses to send (fail closed).

**Affects** `.env` (`WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_FAKE_SEND`), `app/whatsapp.py`.

**How to check** With the venv active:
`.venv\Scripts\python -c "import asyncio; from app.whatsapp import send_text; asyncio.run(send_text('<your number, digits only>', 'test'))"`
→ the message arrives on your phone. Note: the temporary token expires after 24h.

## 3. Webhook URL (ngrok) so Meta can reach your laptop

**What to do**
1. Install ngrok (ngrok.com, free account) and run `ngrok http 8001`.
2. In Meta: WhatsApp → Configuration → Webhook: URL `https://<your-ngrok>/webhook`,
   Verify token = your `WA_VERIFY_TOKEN`. Click **Verify and save**, subscribe to **messages**.

**Why** Meta must be able to call `POST /webhook` from the internet.

**Affects** Meta dashboard only (no code change).

**How to check** Meta says "Verified", and sending "hello" from your phone shows
`📥 Queued message from ****1234` in the API terminal.

## 4. `PHONE_HASH_KEY` (shared team secret, now required)

**What to do**
1. ONE person on the team generates it once:
   `python -c "import secrets; print(secrets.token_hex(32))"`
2. Share it privately (password manager), not in git or group chats.
3. Put it in `services\messaging\.env` as `PHONE_HASH_KEY=<value>`.
   Engineer C uses the same value (handbook section 5).

**Why** `app/security.py hash_phone()` turns phone numbers into HMAC hashes, so we
never store or send raw phones. Without the key the app refuses to start.
If two people use different keys, users can't be found.

**Affects** `.env` (`PHONE_HASH_KEY`), `app/config.py`, `app/security.py`.

**How to check** `.venv\Scripts\python -c "from app.security import hash_phone; print(hash_phone('8801700001234')[:8])"`
prints the same 8 characters on every teammate's machine.

## 5. `INTERNAL_API_KEY`, `MONEY_URL`, `IDENTITY_URL` (now required)

**What to do**
1. Use the team's shared `INTERNAL_API_KEY` (one person generates it with
   `python -c "import secrets; print(secrets.token_hex(32))"` and shares it privately).
   It must be the SAME value in messaging, money and identity `.env` files.
2. In `services\messaging\.env` set `INTERNAL_API_KEY=<value>`,
   `MONEY_URL=http://localhost:8002`, `IDENTITY_URL=http://localhost:8003`.

**Why** `app/clients.py` sends this key as `X-Internal-Key` on every call to B and C;
they answer 401 without it. The app refuses to start if these are missing.

**Affects** `.env`, `app/config.py`, `app/clients.py`.

**How to check** Run the mocks (each in its own terminal, from `services\messaging`):
`uvicorn tests.mocks.mock_money:app --port 8002` and
`uvicorn tests.mocks.mock_identity:app --port 8003`, then
`.venv\Scripts\python -c "import asyncio; from app.clients import MoneyClient; print(asyncio.run(MoneyClient().get_rate('BDT_IDR')))"`
→ prints a rate of 135.20. (Stop the mocks before running Engineer B's real money service on 8002.)

## 6. Anthropic API key (for the AI agent)

**What to do**
1. Go to console.anthropic.com → sign in → **API Keys** → **Create Key**. Copy it.
2. In `services\messaging\.env` add `ANTHROPIC_API_KEY=<your key>` (and optionally
   `LLM_MODEL=claude-opus-5-5`, which is already the default).
3. Never paste the key into chat, code, or git. Set a monthly spend limit in the console.

**Why** `app/agent.py` uses Claude to understand messages like "convert 10000 taka".
Without a key the AI is off: every text gets the 3-button menu (Rates / My rules /
Freeze), and `FREEZE` still works. That's the fake path the tests use.

**Affects** `.env` (`ANTHROPIC_API_KEY`, `LLM_MODEL`), `app/config.py`, `app/agent.py`.

**How to check** Run the api, the worker and the mocks (`uvicorn tests.mocks.mock_money:app --port 8002`,
`uvicorn tests.mocks.mock_identity:app --port 8003`), then send "what's the taka to rupiah rate?"
on WhatsApp → the answer mentions 135.20. With the key removed you get the button menu instead.

## 7. Run all three messaging processes

**What to do** In three terminals (venv active, from `services\messaging`):
`uvicorn app.main:app --port 8001`, `python -m app.worker`, `python -m app.notifier`.

**Why** The api only queues messages; the worker answers them; the notifier sends receipts
and approval links from Money/Identity events.

**How to check** `python -m scripts.push_fake_event transfer_settled` → the notifier
terminal shows `[fake send] text to ****1234` (or the message arrives with `WA_FAKE_SEND=false`).
