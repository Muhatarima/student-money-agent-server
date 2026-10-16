# Things only you can do (one ordered checklist)

Do them **in this order**. Each step says what to do, why, where it goes, and how to check it.
Everything already has a fake path, so the code and all tests run without any of this.

**Golden rules for every step:** secrets go only into `.env` files (never committed) or
Railway variables, plus your password manager. Never paste them into chat, code, or git.

Contract change needed: **none**.

---

## Part A: run everything on your laptop

### ☐ A1. Check your tools
**Do:** Docker Desktop is installed and **running** (whale icon in the taskbar). In PowerShell:
`docker compose version` and `python --version` (3.12 is what production uses).
**Why:** Postgres and Redis run in Docker; each service has its own Python venv.
**Check:** from the repo root, `docker compose up -d` then `docker compose ps` shows
`postgres` and `redis` as running.

### ☐ A2. Make the team's dev secrets (one person, once)
**Do:** in PowerShell:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"   # 3 times: INTERNAL_API_KEY, PHONE_HASH_KEY, WA_VERIFY_TOKEN
```
and, from `services\identity` with its venv active (see A3):
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # 3 times: FERNET_KEY, VAULT_MASTER_KEY, SESSION_SECRET
```
Save all six in a password manager and share them privately with teammates (not in group chats).
**Why:** `INTERNAL_API_KEY` lets the services trust each other; `PHONE_HASH_KEY` turns phone
numbers into hashes; the Fernet keys encrypt WhatsApp ids and documents.
**Where:** the `.env` files in A3. **Check:** you have six named values saved.

### ☐ A3. Create the three `.env` files
**Do:** for each of `services\messaging`, `services\money`, `services\identity`:
```powershell
cd services\<name>
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```
Then open each `.env` in your editor and fill in:
- **all three:** `INTERNAL_API_KEY` = the SAME value from A2.
- **messaging:** `PHONE_HASH_KEY`, `WA_VERIFY_TOKEN` (from A2),
  `REDIS_URL=redis://localhost:6379/0`, keep `WA_FAKE_SEND=true` for now.
- **money:** keep `ENV=dev` (needed for test money); nothing else to change.
- **identity:** `FERNET_KEY`, `VAULT_MASTER_KEY`, `SESSION_SECRET` (from A2, all different).

**Why:** each service refuses to start if a required setting is missing (the error names it).
**Check:** in each service folder: `.venv\Scripts\python -c "from app.config import settings; print('ok')"` prints `ok`.

### ☐ A4. Create the databases and tables (once)
**Do:** from the repo root:
```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money_test;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity_test;"
```
("already exists" errors are fine.) Then in `services\money` and in `services\identity`
(venv active): `alembic upgrade head`.
**Why:** the tables are created by migrations; the `_test` databases are used by `pytest`.
**Check:** `alembic current` prints `0004 (head)` in money and `0002 (head)` in identity.
In each service, `pytest -q` passes.

### ☐ A5. Run the whole-system test (no real accounts needed)
**Do:**
```powershell
cd tests\e2e
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
```
**Why:** it starts all 7 processes in Docker and plays the full demo with fake WhatsApp,
fake AI and a software fingerprint. If this passes, the code is fine and any later problem
is a setting or an account.
**Check:** `1 passed` (about 2 minutes; the first run builds images and takes longer). If
it times out once, run it again: Docker Desktop on Windows sometimes pauses.

### ☐ A6. Meta (WhatsApp) developer app + the allowed list
**Do:**
1. developers.facebook.com → **My Apps** → **Create App** → type **Business** → add the
   **WhatsApp** product.
2. **WhatsApp** → **API Setup**: copy the **Phone Number ID** and the **temporary access
   token** (it lasts 24 hours).
3. Same page, **To** field → **Manage phone number list** → **Add phone number** → your
   own WhatsApp number → enter the code WhatsApp sends you. Add every tester's phone the
   same way (the test number can only message numbers on this **allowed list**).
4. **App settings** → **Basic** → **App secret** → **Show** → copy.
5. In `services\messaging\.env`: `WA_APP_SECRET=<app secret>`,
   `WA_ACCESS_TOKEN=<token>`, `WA_PHONE_NUMBER_ID=<id>`, `WA_FAKE_SEND=false`.

**Why:** Meta must accept our token to deliver replies; the app secret proves incoming
webhooks really come from Meta.
**Check:** in `services\messaging` (venv active): `python scripts/send_test_message.py`,
type your number → the message arrives. Error `131030` = your number isn't on the allowed list.

### ☐ A7. ngrok, so Meta can reach your laptop
**Do:**
1. Sign up at ngrok.com, install it, and run the `ngrok config add-authtoken ...` line
   from your ngrok dashboard once.
2. Start the messaging api (`uvicorn app.main:app --port 8001` in `services\messaging`),
   then in another terminal: `ngrok http 8001`. Copy the `https://....ngrok-free.app` URL.
3. Meta → **WhatsApp** → **Configuration** → **Webhook** → **Edit**: Callback URL
   `https://<ngrok-url>/webhook`, Verify token = your `WA_VERIFY_TOKEN` → **Verify and
   save** → **Manage** → subscribe to **messages**.

**Why:** Meta calls `POST /webhook` from the internet; ngrok forwards it to your laptop.
**Check:** Meta shows the webhook as verified; when you send "hi" from your phone, the api
terminal shows `queued message from ****1234`. A new ngrok URL (after restarting ngrok)
means repeating step 3.

### ☐ A8. Anthropic API key (the AI)
**Do:** console.anthropic.com → sign in → **API Keys** → **Create Key** → copy it into
`services\messaging\.env` as `ANTHROPIC_API_KEY=<key>`. Then **Billing / Limits** → set a
small monthly spend limit.
**Why:** without a key the AI is off and users only get the 3-button menu (FREEZE still works).
**Check:** after A9, "what's the taka to rupiah rate?" gets an answer with the rate.

### ☐ A9. Run all 7 processes and try the demo from your phone
**Do:** one terminal each (venv active, in the service folder):
- `services\money`: `uvicorn app.main:app --port 8002` and `python -m app.jobs`
- `services\identity`: `uvicorn app.main:app --port 8003` and `python -m app.jobs`
- `services\messaging`: `uvicorn app.main:app --port 8001`, `python -m app.worker`,
  `python -m app.notifier`
- plus `ngrok http 8001` from A7.

Then follow the demo in the handbook (`docs/00-START-HERE-shared-contracts.md`, section 10).
To give yourself test money after linking:
1. In `services\messaging` (venv active): `python -c "from app.security import hash_phone; print(hash_phone('<your number>'))"`
2. http://127.0.0.1:8003/docs → `GET /internal/users/by-phone/{phone_hash}` with that hash
   and header `X-Internal-Key` → copy `user_id`.
3. http://127.0.0.1:8002/docs → `POST /dev/seed/{user_id}` (same header) → 10000 BDT.

**Passkey on the phone:** the link opens `http://localhost:8003`, which only works on the
laptop itself. To use the phone's fingerprint, also run `ngrok http 8003` and set in
`services\identity\.env`: `PUBLIC_BASE_URL=https://<that-ngrok-url>` and
`RP_ID=<that-ngrok-host>` (no `https://`), then restart identity. If your ngrok plan allows
only one tunnel, test passkeys on the laptop (Windows Hello) and do the phone test after
deploying (Part B).
**Check:** you get the receipt "✅ Converted ..." in WhatsApp.

---

## Part B: put it online (Railway + Cloudflare R2)

`DEPLOY.md` has the exact clicks. These are the steps only you can do, in order.

### ☐ B1. Review and merge the work into `main`
**Do:** on GitHub, open a pull request from `build/integration` into `main`, read it, and
merge it. (The finished work is on `build/integration`; nothing was ever committed to `main`.)
Also recommended: **Settings** → **Branches** → protect `main` (require a review).
**Why:** Railway deploys one branch; it should be reviewed code.
**Check:** `main` on GitHub has `DEPLOY.md` and `tests/e2e`.

### ☐ B2. New production secrets
**Do:** `DEPLOY.md` step 1: six NEW values (never the dev ones), saved in the password manager.
**Why:** dev keys have lived on laptops and in `.env` files; production must not share them.
**Check:** none of the production values matches a value in any `.env` file.

### ☐ B3. Cloudflare R2 bucket for documents
**Do:** `DEPLOY.md` step 2: a **private** bucket and an API token for **only** that bucket.
**Why:** identity refuses to start in production with local storage (fail closed), and
Railway disks are not for sensitive files.
**Check:** the bucket page says "Public access: Disabled"; you saved 4 `S3_*` values.

### ☐ B4. Railway: databases, 7 services, variables, deploy
**Do:** `DEPLOY.md` steps 3 to 7.
**Why:** this is the hosting. Only `messaging-api` and `identity-api` get public domains.
**Check:** `https://<identity domain>/health` and `https://<messaging domain>/health` show
`{"status":"ok"}`; `money-api` has no public domain.

### ☐ B5. Point Meta at Railway + permanent token
**Do:** `DEPLOY.md` step 8: webhook → `https://<messaging domain>/webhook`, then a
**System User permanent token** in `WA_ACCESS_TOKEN` (the 24-hour token would stop working).
Testers still need to be on the allowed list (A6 step 3) until your Meta business is verified
and you add a real phone number.
**Check:** with your laptop OFF, "hi" from your phone gets the link button.

### ☐ B6. Sign the production checklist and run the demo on production
**Do:** `DEPLOY.md` step 9 (tick every box), then the handbook demo from a real phone.
**Check:** all boxes ticked; the demo works end to end.

### ☐ B7. (Optional) Your own domain
**Do:** buy a domain, then in Railway `identity-api` → **Networking** → **Custom Domain**,
add the CNAME it shows at your registrar, and update `PUBLIC_BASE_URL` and `RP_ID`.
**Do it before real users register:** passkeys are tied to the domain, so changing it later
means everyone registers again.
**Check:** `https://<your domain>/health` → `{"status":"ok"}`.

---

## Part C: before REAL money (not part of this MVP; don't skip later)

Today money moves only through the fake `MockProvider`. Before any real money:

1. **Licensed payment/FX provider:** partner with one (they hold funds and check identity),
   then write a real provider next to `services/money/app/providers/mock.py`.
2. **Legal advice** on Bangladesh Bank foreign-exchange rules for your exact money flow.
3. **Independent penetration test** of all three services.
4. Move encryption keys to **AWS KMS** (or similar) and long workflows to **Temporal**.
5. A **closed beta** with tiny limits (`MAX_PER_TX_BDT`, `MAX_PER_DAY_BDT`).
