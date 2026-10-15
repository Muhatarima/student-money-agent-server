# Engineer C: Identity, Web Pages, Vault & Deployment (from scratch to deployment)

> **Read `00-START-HERE-shared-contracts.md` first.** You must know the contracts (section 6) and the Claude Code workflow (section 8).

## Your mission
You make sure **only the real user can approve anything**, and you own the team's infrastructure:
1. **Repo, CI, docker-compose** (Day 0, for the whole team)
2. **Users**: link a WhatsApp number to an account (phone stored hashed + encrypted)
3. **Passkeys**: fingerprint / Face ID registration and login (WebAuthn)
4. **Approval pages**: show exactly what is being approved, then sign it with a passkey
5. **Signature verification** for auto-rules (B asks you before every run)
6. **Document vault**: encrypted upload, auto-delete
7. **Audit log**: a tamper-evident record of every sensitive action
8. **Deployment** of all 3 services to Railway

**Your security promise:** *"Only the real user can approve, and every approval is for exactly what they saw."*

**You own:** `services/identity/`, port **8003**, Postgres database `identity`, repo infra.
**You never touch:** WhatsApp sending, the LLM, ledger or transfer logic.

## Final folder structure
```
services/identity/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py              ← FastAPI app, routers, security headers
│   ├── auth.py              ← X-Internal-Key dependency
│   ├── db.py
│   ├── models.py            ← users, passkeys, link_tokens, approvals, signatures, documents, audit_events
│   ├── crypto.py            ← Fernet helpers, token hashing
│   ├── users.py             ← internal user endpoints
│   ├── passkeys.py          ← WebAuthn register/login
│   ├── approvals.py         ← create + page + confirm
│   ├── signatures.py        ← verify-signature
│   ├── vault.py             ← upload, encrypt, purge
│   ├── audit.py             ← hash-chained audit log
│   ├── storage.py           ← local folder (dev) / S3-compatible R2 (prod)
│   ├── events.py            ← publish to events:notify
│   └── jobs.py              ← purge expired documents/tokens
├── templates/               ← Jinja2 HTML pages
├── static/                  ← passkey.js
├── alembic/
├── tests/
├── mock_server.py
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## DAY 0: Team infrastructure (do this FIRST, with A and B watching)

### 🤖 Prompt for Claude Code (run in an EMPTY repo folder)
```
I'm Engineer C setting up the monorepo for the whole team. Follow
docs/00-START-HERE-shared-contracts.md exactly (I'll paste it into docs/ first).

Create:
1. Folder layout from section 4 (empty service folders with a README placeholder each).
2. CLAUDE.md with the exact content from section 8.
3. .gitignore from section 7.
4. docker-compose.yml from section 7.
5. packages/contracts as an installable package (pyproject.toml, setuptools) containing
   contracts/models.py and contracts/events.py EXACTLY as in section 6.1 and 6.3, and
   contracts/hashing.py from section 6.4. Add tests in packages/contracts/tests that
   payload_hash is stable regardless of dict key order.
6. .github/workflows/ci.yml: on push and pull_request; job matrix over
   services/messaging, services/money, services/identity; Python 3.12; services
   postgres:16 and redis:7; steps: pip install -r requirements.txt (if the file exists),
   ruff check ., pytest -q (skip gracefully if no tests yet). Also a job testing
   packages/contracts.
7. A root README.md: what the project is, how to start docker compose, links to the 4 guides.
```

### ✅ Verify
```powershell
docker compose up -d
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity;"
git add . ; git commit -m "Day 0: repo skeleton + contracts + CI" ; git push
```
On GitHub → **Actions** tab → the CI run is green. Then add A and B as collaborators. Turn on **branch protection** for `main` (require CI to pass and 1 review).

---

## STEP 0: Your service setup

```powershell
cd services\identity
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 🤖 Prompt for Claude Code
```
I'm Engineer C. Work only inside services/identity. Read CLAUDE.md first.
Create:
1. requirements.txt: fastapi, uvicorn[standard], pydantic, pydantic-settings,
   python-dotenv, sqlalchemy[asyncio], asyncpg, alembic, redis, httpx, jinja2,
   python-multipart, webauthn (py_webauthn), cryptography, boto3, filetype,
   pytest, pytest-asyncio, fakeredis, aiosqlite, ruff, "-e ../../packages/contracts"
2. app/config.py (pydantic-settings): DATABASE_URL, REDIS_URL, INTERNAL_API_KEY,
   PHONE_HASH_KEY, MONEY_URL, PUBLIC_BASE_URL, RP_ID (e.g. "localhost"),
   RP_NAME ("Student Money Agent"), FERNET_KEY (for wa_id encryption),
   VAULT_MASTER_KEY (Fernet key), SESSION_SECRET, STORAGE_BACKEND ("local"|"s3"),
   S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, ENV ("dev"|"production").
3. app/auth.py require_internal_key (compare_digest on X-Internal-Key).
4. app/db.py, app/main.py with GET /health, Jinja2Templates("templates"), StaticFiles.
5. .env.example. Also print a one-liner to generate Fernet keys:
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### ✅ Verify
```powershell
pip install -r requirements.txt
copy .env.example .env     # fill real values; generate 2 Fernet keys
uvicorn app.main:app --reload --port 8003
```
http://127.0.0.1:8003/health → `{"status":"ok"}`

---

## DAY 1: Users, link tokens, internal endpoints

### Step 1.1: Tables + audit log
#### 🤖 Prompt for Claude Code
```
Create app/models.py (SQLAlchemy 2.0, UUID PKs, timezone timestamps):
- users(id, phone_hash UNIQUE, wa_id_encrypted (Fernet), linked bool default false,
  frozen bool default false, created_at)
- passkeys(id, user_id FK, credential_id bytes UNIQUE, public_key bytes, sign_count int,
  transports text, device_label text, created_at, last_used_at)
- link_tokens(id, token_hash UNIQUE (sha256 of the token), phone_hash, wa_id_encrypted,
  expires_at, used_at nullable)
- approvals(id, user_id, kind, subject_id, payload_hash, summary JSON, token_hash UNIQUE,
  nonce, expires_at, state "pending"|"approved"|"expired"|"rejected", created_at)
- signatures(id, user_id, approval_id, credential_id, payload_hash, nonce,
  authenticator_data bytes, client_data_json bytes, signature bytes, created_at)
- documents(id, user_id, doc_type, storage_key, wrapped_key bytes, content_type,
  size_bytes, delete_after, deleted_at nullable, created_at)
- audit_events(id bigserial, ts, actor, action, target_hash, details JSON (no PII),
  prev_hash, row_hash)
Alembic async setup + first migration + second migration with a trigger that blocks
UPDATE/DELETE on audit_events.

Create app/audit.py: async audit(session, actor, action, target, details):
row_hash = sha256(prev_hash + canonical_json(fields)); target is stored only as a hash.
Add verify_chain(session) -> bool that recomputes all hashes. Tests: chain verifies;
editing a row (bypassing the trigger in the test DB) breaks verification.
```

### Step 1.2: Internal user endpoints + link tokens
#### 🤖 Prompt for Claude Code
```
Create app/crypto.py: encrypt_str/decrypt_str with FERNET_KEY, new_token() (32 random
bytes urlsafe), hash_token(t) sha256 hex.

Create app/users.py router (all /internal/* protected by require_internal_key):
- GET /internal/users/by-phone/{phone_hash} -> UserOut (404 if none)
- POST /internal/link-tokens body {"phone_hash": str, "wa_id": str}
  -> creates token (store ONLY its hash), expires in 10 minutes,
  returns LinkTokenOut with url PUBLIC_BASE_URL + "/link?t=<token>".
  (Note to team: A sends wa_id too, stored encrypted; tell A to include it.)
- GET /internal/users/{user_id}/wa-target -> {"wa_id": decrypted}
- POST /internal/users/{user_id}/freeze -> frozen = true, expire all pending approvals,
  audit "user.frozen", publish event "frozen".
Rate-limit link token creation per phone_hash: max 5 per hour (Redis).
Tests for each endpoint: missing key 401, wrong key 401, happy paths, expired token.
```

---

## DAY 2: Passkeys (fingerprint / Face ID)

### How it works (simple version)
1. The server creates a random **challenge**.
2. The phone's fingerprint sensor **signs** the challenge with a private key that **never leaves the phone**.
3. The server checks the signature using the **public key** it saved at registration.

No passwords. Nothing secret is stored on the server.

### Step 2.1: Link + register page
#### 🤖 Prompt for Claude Code
```
Implement passkey registration with py_webauthn (server) and @simplewebauthn/browser
(browser) loaded from unpkg with an EXACT pinned version (UMD bundle,
global SimpleWebAuthnBrowser). Put browser code in static/passkey.js.

Pages/routes:
- GET /link?t=<token>: validate token (exists by hash, not used, not expired);
  show templates/link.html: "Secure your account" + button "Use fingerprint / Face ID".
- POST /passkeys/register/options {t}: generate_registration_options(rp_id=RP_ID,
  rp_name, user_id=bytes of (new or existing) user id, user_name="student",
  authenticator_selection: resident key preferred, user_verification REQUIRED),
  store the challenge in Redis "webauthn:reg:{token_hash}" with 5 min TTL.
- POST /passkeys/register/verify {t, credential}: verify_registration_response with
  expected_challenge from Redis (then delete it), expected_origin=PUBLIC_BASE_URL,
  expected_rp_id=RP_ID, require_user_verification=True.
  On success, in ONE transaction: create the user if needed (phone_hash, wa_id_encrypted
  from the link token), save the passkey, linked = true, mark the token used,
  audit "passkey.registered", publish event "account_linked".
- templates/done.html "All set ✅ You can go back to WhatsApp."
All POST endpoints: JSON only, check Origin header == PUBLIC_BASE_URL.
Tests: mock the webauthn verify functions; token reuse fails; expired token fails;
challenge reuse fails.
```

#### ✅ Verify
1. Generate a link token via http://127.0.0.1:8003/docs (with the internal key).
2. Open the returned URL **on the same laptop** (`localhost` works without HTTPS).
3. Click the button → Windows Hello / fingerprint → "All set ✅".
4. Check in the DB that the user is `linked = true`.

> 📱 **Testing on your phone:** run `ngrok http 8003`, set `PUBLIC_BASE_URL` to the ngrok URL and `RP_ID` to the ngrok hostname (without `https://`). Passkeys are tied to the domain, so if the ngrok URL changes you must register again. In production, the fixed domain solves this.

### Step 2.2: Passkey login (used by approvals and the vault)
#### 🤖 Prompt for Claude Code
```
Add generic authentication helpers in app/passkeys.py:
- start_auth(user_id, challenge_bytes) -> options JSON with allow_credentials = the
  user's credential ids, user_verification REQUIRED. (Challenge is passed in, so callers
  can bind it to a payload.)
- finish_auth(user_id, credential, expected_challenge) -> passkey row
  using verify_authentication_response with the stored public key and sign_count;
  update sign_count and last_used_at. Unknown credential or wrong user -> fail.
Tests with mocked verification.
```

---

## DAY 3: Approval pages (the most important page in the product)

### What makes an approval safe
- The page shows **exactly** what B sent (rate, fee, amount, recipient, expiry).
- The passkey challenge is **derived from the payload hash**: `challenge = sha256(payload_hash + nonce)`. The fingerprint signature therefore **mathematically covers what was shown**. Change one digit and the signature fails.
- The link is **single-use**, **expires**, and works only for that user's passkeys.

### 🤖 Prompt for Claude Code
```
Create app/approvals.py:

1. POST /internal/approvals (protected, ApprovalCreate -> ApprovalCreated):
   refuse if user frozen or not linked (409). Create approval with random nonce and
   a random URL token (store hash only). expires_at = min(request.expires_at, now+10min).
   url = PUBLIC_BASE_URL + "/approve/{approval_id}?t=<token>". Audit "approval.created".

2. GET /approve/{approval_id}?t=...: validate token hash, state pending, not expired,
   user not frozen. Render templates/approve.html showing the summary fields in a
   clear table (amount, from->to, rate, fee, you receive, provider, recipient masked),
   a live countdown to expires_at, a big "Approve with fingerprint" button and a
   "Cancel" button. For kind "rule" show the rule fields (target rate, amount per run,
   monthly max, valid until) and the text "This rule will run automatically within
   these limits until you freeze it."
   Expired -> page "This request expired. Ask again in WhatsApp."

3. POST /approve/{approval_id}/options {t}: challenge = sha256(payload_hash + nonce) bytes;
   start_auth(user_id, challenge).

4. POST /approve/{approval_id}/verify {t, credential}:
   finish_auth with the same derived challenge; in ONE transaction: state pending ->
   approved (SELECT FOR UPDATE; if not pending -> 409), store a signatures row
   (authenticator_data, client_data_json, signature, nonce, payload_hash, credential_id),
   audit "approval.approved". Then call B: POST {MONEY_URL}/internal/approvals/confirmed
   with ApprovalConfirmed(approval_id, subject_id, payload_hash, signature_id).
   If B returns non-2xx: show "Something went wrong, no money was moved" and audit it.
   Render templates/approved.html "Approved ✅ You'll get a receipt in WhatsApp."

5. POST /approve/{approval_id}/cancel {t}: state rejected, audit.

6. tests/mocks/mock_money.py: a fake B accepting /internal/approvals/confirmed.
Tests: tampered summary in the DB -> signature check fails; token reuse -> 409;
expired -> no call to B; frozen user -> refused; another user's passkey -> refused.
```

#### ✅ Verify
1. Run the mock money server on 8002.
2. `POST /internal/approvals` via `/docs` with a fake transfer summary → open the URL.
3. You see the amount, rate, fee and countdown → fingerprint → "Approved ✅", and the mock money server prints the confirmation.
4. Open the same link again → "already used". ✅

---

## DAY 4: Signature verification for auto-rules + mocks for teammates

### 🤖 Prompt for Claude Code
```
1. app/signatures.py: POST /internal/verify-signature (protected, VerifySignatureIn ->
   VerifySignatureOut). Load the signatures row by id; it must belong to user_id; its
   payload_hash must equal the request payload_hash (compare_digest); user must not be
   frozen; the passkey must still exist (user may have removed it). Re-verify the stored
   assertion cryptographically with py_webauthn verify_authentication_response using the
   stored client_data_json/authenticator_data/signature, expected_challenge =
   sha256(payload_hash + nonce), and credential_current_sign_count=0 (re-verification of
   an old assertion must not fail on the counter). Any problem -> {"valid": false}
   (fail closed). Audit "signature.verified" or "signature.rejected".
   Tests: valid, wrong payload hash, frozen user, deleted passkey, wrong user.

2. mock_server.py at service root: a FAKE Identity service for teammates implementing
   ALL C endpoints from the contract with fake data (by-phone returns a linked user,
   approvals return a url, verify-signature returns valid true, wa-target returns
   "8801700000000"). Runnable: uvicorn mock_server:app --port 8003.
   Commit it and tell A and B.
```

---

## DAY 5: Document vault + hardening

### Step 5.1: Vault (envelope encryption)
**Idea:** each document gets its **own key**. That key is encrypted ("wrapped") with the master key. To delete a document permanently, destroy its wrapped key. Any leftover copy is then unreadable.

#### 🤖 Prompt for Claude Code
```
Create app/storage.py: LocalStorage (folder vault_local/, dev only) and S3Storage
(boto3, S3-compatible endpoint, e.g. Cloudflare R2) with put(key, bytes), get(key), delete(key).

Create app/vault.py:
- POST /internal/upload-links (protected) {user_id} -> url PUBLIC_BASE_URL/upload/{id}?t=...
  (single-use token, 15 min).
- GET /upload/{id}?t=...: page that first requires passkey login (reuse start_auth with a
  random challenge), then shows an upload form (doc_type: passport|visa|admission_letter|other).
  After passkey success set a short-lived (10 min) signed session cookie: HttpOnly, Secure
  (when not localhost), SameSite=Strict.
- POST /upload/{id}: requires that session + CSRF token. Limits: max 10 MB; allowed
  types pdf/jpeg/png checked by MAGIC BYTES (filetype library), not the filename.
  data_key = Fernet.generate_key(); ciphertext = Fernet(data_key).encrypt(bytes);
  wrapped_key = Fernet(VAULT_MASTER_KEY).encrypt(data_key); storage key = random uuid
  (never the filename); delete_after = now + 7 days. Audit "document.uploaded"
  (doc_type only, no file name). Page shows "Stored securely. Auto-deletes on <date>."
- No endpoint returns document contents in the MVP (nobody needs to read them yet).
- app/jobs.py (python -m app.jobs): every 10 minutes purge documents past delete_after:
  storage.delete, set wrapped_key = NULL, deleted_at = now, audit "document.purged".
  Also purge expired link tokens and approvals (mark expired).
Tests: wrong file type rejected (an .exe renamed .pdf), too large rejected, purge
removes the file and wrapped key, upload without passkey session rejected.
```

### Step 5.2: Hardening
#### 🤖 Prompt for Claude Code
```
Hardening for services/identity:
1. Middleware adding security headers on every response: Content-Security-Policy
   (default-src 'self'; script-src 'self' https://unpkg.com; frame-ancestors 'none'),
   Strict-Transport-Security (production only), X-Content-Type-Options nosniff,
   Referrer-Policy no-referrer, X-Frame-Options DENY, Cache-Control no-store on pages.
2. Rate limits (Redis): passkey verify endpoints max 10/min per IP and per approval/token.
3. All tokens compared via hash lookups; never log tokens, wa_id or phone_hash.
4. /health checks DB and Redis.
5. When ENV=production refuse to start if PUBLIC_BASE_URL is not https.
6. Run ruff, make all tests pass, then write services/identity/README.md.
```

---

## DAY 6: Integration (with A and B)
1. Stop your mocks. Set `MONEY_URL=http://localhost:8002` to point at the real B.
2. Run `uvicorn app.main:app --port 8003` and `python -m app.jobs`.
3. If testing from phones, run `ngrok http 8003` and update `PUBLIC_BASE_URL` and `RP_ID`.
4. Run the handbook demo script with the team. Your parts: linking, approval pages, rule signing, verify-signature, upload.

---

## DAY 7: Deployment of the WHOLE system (you lead)

### Step 7.1: Dockerfiles
#### 🤖 Prompt for Claude Code
```
Create services/identity/Dockerfile (python:3.12-slim, non-root user, build context repo
root, install packages/contracts + requirements, copy templates/static). CMD:
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8003}
--proxy-headers. Add .dockerignore. Then check that services/messaging and
services/money also have Dockerfiles following the same pattern (tell me if missing).
```

### Step 7.2: Railway setup (step by step)
1. Go to **railway.app** → sign in with GitHub → **New Project** → **Deploy from GitHub repo** → choose your repo.
2. **Add plugins:** `+ New` → **Database → PostgreSQL** (create two: `pg-money`, `pg-identity`) and `+ New` → **Database → Redis**.
3. **Create the services.** For each one: `+ New → GitHub Repo` → same repo → **Settings**:
   - **Dockerfile path:** `services/<name>/Dockerfile`
   - **Root directory:** repo root (so contracts are included)
   - Custom start command where needed:

| Railway service | Dockerfile | Start command | Public domain? |
|---|---|---|---|
| `messaging-api` | messaging | default | ✅ yes (Meta webhook) |
| `messaging-worker` | messaging | `python -m app.worker` | ❌ |
| `messaging-notifier` | messaging | `python -m app.notifier` | ❌ |
| `money-api` | money | default | ❌ (private only) |
| `money-jobs` | money | `python -m app.jobs` | ❌ |
| `identity-api` | identity | default | ✅ yes (approval pages) |
| `identity-jobs` | identity | `python -m app.jobs` | ❌ |

4. **Variables** (each service's **Variables** tab). Use Railway **reference variables** for DB/Redis URLs:
   - `DATABASE_URL`: from the right Postgres, with the prefix changed to `postgresql+asyncpg://`
   - `REDIS_URL`: from the Redis plugin
   - `INTERNAL_API_KEY`, `PHONE_HASH_KEY`, `FERNET_KEY`, `VAULT_MASTER_KEY`, `SESSION_SECRET`: **new production values** (never reuse dev keys)
   - `MONEY_URL`, `IDENTITY_URL`: the **private** network URLs (e.g. `http://money-api.railway.internal:8002`)
   - Identity: `PUBLIC_BASE_URL=https://<identity domain>`, `RP_ID=<identity domain without https>`, `ENV=production`, `STORAGE_BACKEND=s3` + R2 values
5. **Document storage:** Railway disks are not for sensitive files. Create a **Cloudflare R2** bucket (private, no public access) and an API token limited to that bucket, then put the values in the identity variables.
6. **Deploy** → watch the logs of each service for errors and confirm the migrations ran.
7. **Domains:** generate Railway domains for `messaging-api` and `identity-api` (or attach your own domain).
8. **Tell A** to set the Meta webhook to `https://<messaging domain>/webhook`.

### Step 7.3: Production checklist (you sign it off)
- [ ] All secrets are new production values, only in Railway variables, not in git
- [ ] `money-api` has **no public domain**
- [ ] Identity `ENV=production`, HTTPS URL, correct `RP_ID`
- [ ] The R2 bucket is private
- [ ] `/dev/*` routes are disabled in money (B confirms)
- [ ] Small money limits in production (B confirms)
- [ ] Meta permanent system-user token in use (A confirms)
- [ ] CI is green on `main`; branch protection is on
- [ ] Audit chain verifies (`verify_chain` script)
- [ ] The handbook demo script passes on production

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Passkey button does nothing | Open the browser console (F12). Check that the unpkg script loaded and the CSP allows unpkg |
| `NotAllowedError` in browser | The user cancelled, or `RP_ID` doesn't match the page domain |
| Verification fails "origin mismatch" | `PUBLIC_BASE_URL` must exactly match the browser URL (scheme + host + port) |
| Works on laptop, not phone | The phone can't reach `localhost`. Use ngrok and update `PUBLIC_BASE_URL` and `RP_ID` |
| Passkeys stopped working after the ngrok restart | The new domain means new passkeys are needed (normal in dev) |
| Railway service crashes on start | Check its logs for a missing env var. The config should name it |
| `asyncpg` URL error on Railway | Change `postgresql://` to `postgresql+asyncpg://` |

## ✅ Your done checklist
- [ ] Repo, CI, docker-compose, contracts package, branch protection
- [ ] Users with hashed phone + encrypted wa_id; internal endpoints protected
- [ ] Link → passkey registration → linked; tokens single-use
- [ ] Approval page bound to payload hash; single-use; expiry; frozen users refused
- [ ] verify-signature re-checks cryptographically; fails closed
- [ ] Vault: passkey-gated upload, magic-byte checks, envelope encryption, auto-purge
- [ ] Hash-chained, append-only audit log
- [ ] Security headers, rate limits
- [ ] All 7 Railway services deployed; production checklist signed
