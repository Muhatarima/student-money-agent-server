# Identity Service (Engineer C)

Port: **8003** | Service: **`services/identity`** | Database: **`identity` (PostgreSQL)**

The Identity service ensures that **only the real user can approve financial transactions**, verifies biometric mandate signatures for autonomous rules, securely manages user documents with envelope encryption, and maintains an append-only, tamper-evident audit log.

---

## Features

1. **User Identity & Phone Linking**:
   - Phone numbers are stored only as cryptographic SHA-256 hashes (`phone_hash`).
   - WhatsApp IDs (`wa_id`) are stored encrypted at rest using Fernet symmetric encryption.
   - Zero plaintext PII stored in databases.

2. **Passkeys / WebAuthn (TouchID, FaceID, Windows Hello)**:
   - Modern passwordless authentication using `@simplewebauthn/browser` and `py_webauthn`.
   - Single-use, time-limited registration links (10 min TTL).

3. **Cryptographically Bound Approval Flow**:
   - Transaction approvals show exact amounts, exchange rates, recipient details, and live countdowns.
   - WebAuthn signing challenge is derived from: `challenge = sha256(payload_hash + nonce)`.
   - The user's biometric signature mathematically covers the transaction payload.
   - Idempotent and concurrency-safe via database row locking (`SELECT FOR UPDATE`).
   - Confirms directly to the Money service (`POST /internal/approvals/confirmed`).

4. **Mandate Signature Verification**:
   - `POST /internal/verify-signature` verifies stored WebAuthn assertions before autonomous rule runs.
   - Fails closed on any discrepancy, frozen status, or revoked credentials.

5. **Document Vault**:
   - Envelope encryption: each file is encrypted with a unique data key, wrapped by `VAULT_MASTER_KEY`.
   - Magic byte validation using `filetype` (rejects disguised executables).
   - Local disk storage for dev, Cloudflare R2 / S3 storage for production.
   - Auto-purges documents after 7 days (zeros wrapped keys and permanently deletes file bytes).

6. **Tamper-Evident Audit Chain**:
   - Hash-chained audit logging where each row hash incorporates the previous row hash.
   - PostgreSQL trigger blocking `UPDATE` and `DELETE` on the `audit_events` table.
   - Recomputable via `verify_chain()`.

---

## Directory Structure

```
services/identity/
├── app/
│   ├── config.py         # Pydantic settings & env validation
│   ├── main.py           # FastAPI entrypoint & security headers middleware
│   ├── auth.py           # X-Internal-Key protection
│   ├── db.py             # Async SQLAlchemy 2.0 engine & session
│   ├── models.py         # SQLAlchemy ORM models (users, passkeys, approvals, etc.)
│   ├── crypto.py         # Fernet encryption & token utilities
│   ├── audit.py          # Hash-chained tamper-evident audit logging
│   ├── users.py          # Internal user lookup, link tokens, freeze
│   ├── passkeys.py       # WebAuthn registration & auth helpers
│   ├── approvals.py      # Approval display, signing & confirmation callback
│   ├── signatures.py     # Cryptographic signature verification
│   ├── vault.py          # Document upload & envelope encryption
│   ├── storage.py        # Local / S3 storage abstraction
│   ├── events.py         # Redis notification publisher
│   └── jobs.py           # Periodic purge worker for expired docs/tokens
├── alembic/              # Database migrations
├── templates/            # Jinja2 HTML templates (link, done, approve, upload)
├── static/               # Client-side JavaScript (passkey.js)
├── tests/                # Comprehensive async pytest test suite
├── mock_server.py        # Canonical teammate mock server (port 8003)
├── Dockerfile            # Container definition for Railway deployment
└── requirements.txt      # Python dependencies
```

---

## Local Development

### 1. Setup Virtual Environment
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in secrets:
```powershell
copy .env.example .env
```
Generate Fernet keys with:
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run Migrations & Start Server
```powershell
alembic upgrade head
uvicorn app.main:app --reload --port 8003
```
API Documentation is available at: [http://127.0.0.1:8003/docs](http://127.0.0.1:8003/docs)

### 4. Run Background Jobs Worker
In a separate terminal:
```powershell
python -m app.jobs
```

---

## Teammate Mock Server

For Engineers A (Messaging) and B (Money) to test integrations locally without full DB/passkey setup:
```powershell
uvicorn mock_server:app --port 8003
```

---

## Testing & Linting

Run all tests:
```powershell
pytest -v
```

Run linter:
```powershell
ruff check .
```

---

## Production Deployment (Railway)

Engineer C leads the deployment of the entire system across Railway.

### 1. Multi-Service Architecture (7 Services)

| Railway Service | Dockerfile Path | Start Command | Public Domain | Role |
|---|---|---|:---:|---|
| `messaging-api` | `services/messaging/Dockerfile` | Default | ✅ Yes | Meta WhatsApp Webhook |
| `messaging-worker` | `services/messaging/Dockerfile` | `python -m app.worker` | ❌ No | Inbound LLM Intent Worker |
| `messaging-notifier` | `services/messaging/Dockerfile` | `python -m app.notifier` | ❌ No | Redis Outbound Notification Publisher |
| `money-api` | `services/money/Dockerfile` | Default | ❌ No | Private Financial Core API |
| `money-jobs` | `services/money/Dockerfile` | `python -m app.jobs` | ❌ No | Background Autonomous Rule Engine |
| `identity-api` | `services/identity/Dockerfile` | Default | ✅ Yes | Passkey Registration, Approvals, Vault |
| `identity-jobs` | `services/identity/Dockerfile` | `python -m app.jobs` | ❌ No | 7-day Document Purge & Token Expiry Worker |

### 2. Infrastructure Plugins

1. **PostgreSQL 1** (`pg-money`): Database for Money Core.
2. **PostgreSQL 2** (`pg-identity`): Database for Identity & Audit Log.
3. **Redis**: Shared message bus (`events:notify`) and rate limiting.
4. **Cloudflare R2**: Private S3-compatible bucket for encrypted vault documents.

### 3. Identity Environment Variables in Railway

Set in the **Variables** tab of `identity-api` and `identity-jobs`:
- `DATABASE_URL`: `postgresql+asyncpg://...` (from `pg-identity`)
- `REDIS_URL`: `redis://...` (from Redis plugin)
- `INTERNAL_API_KEY`: 32-byte hex shared with `messaging` and `money`
- `PHONE_HASH_KEY`: 32-byte hex shared with `messaging`
- `MONEY_URL`: Internal private URL, e.g., `http://money-api.railway.internal:8002`
- `PUBLIC_BASE_URL`: `https://<your-identity-domain>.up.railway.app`
- `RP_ID`: `<your-identity-domain>.up.railway.app` (domain without `https://`)
- `RP_NAME`: `Student Money Agent`
- `FERNET_KEY`: Base64 32-byte Fernet key
- `VAULT_MASTER_KEY`: Base64 32-byte Fernet key for envelope key wrapping
- `SESSION_SECRET`: 32-byte hex for secure cookie signing
- `STORAGE_BACKEND`: `s3`
- `S3_ENDPOINT`: Cloudflare R2 endpoint URL
- `S3_BUCKET`: Private bucket name
- `S3_ACCESS_KEY` & `S3_SECRET_KEY`: Restricted R2 API credentials
- `ENV`: `production` (enforces HTTPS on public base URL and HSTS)

### 4. Production Security Checklist (Engineer C Sign-off)

- [x] Zero hardcoded secrets in git; all production keys generated freshly.
- [x] `money-api` has **no public domain** (private Railway internal network only).
- [x] Identity `ENV=production` refuses to start unless `PUBLIC_BASE_URL` is `https://`.
- [x] Cloudflare R2 bucket is private with no public read access.
- [x] `/dev/*` routes disabled in `money` in production.
- [x] Strict limits on test transactions enabled in `money`.
- [x] Meta WhatsApp permanent system-user access token configured in `messaging`.
- [x] Tamper-evident hash chain verified with `verify_chain()`.
- [x] CI checks passing on GitHub Actions for contracts, messaging, money, and identity.

