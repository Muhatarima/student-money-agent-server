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
