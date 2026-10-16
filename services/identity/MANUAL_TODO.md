# Things only you can do (identity service)

Everything below has a dev/test path, so the code runs and the tests pass without it.

## 1. Create `services\identity\.env`

**What to do**
1. `copy .env.example .env` (in `services\identity`).
2. Make THREE different Fernet keys and put them in `FERNET_KEY`, `VAULT_MASTER_KEY`, `SESSION_SECRET`:
   `.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. `INTERNAL_API_KEY` = the team's shared value (the same in messaging, money and identity).
4. Run the migrations once: `.venv\Scripts\alembic upgrade head` (Docker running; the
   `identity` database already exists in the local Postgres container).

**Why** The service refuses to start without these. Never reuse dev keys in production.
Losing `VAULT_MASTER_KEY` makes every stored document unreadable (that is the point).

**How to check** `.venv\Scripts\uvicorn app.main:app --port 8003`, then
http://127.0.0.1:8003/health → `{"status":"ok"}`.

## 2. Try a passkey for real (Windows Hello / phone)

**What to do** On the laptop, `localhost` works without HTTPS: create a link token via
http://127.0.0.1:8003/docs (header `X-Internal-Key`), open the URL, tap the button.
On a phone: `ngrok http 8003`, then set `PUBLIC_BASE_URL=https://<id>.ngrok-free.app`
and `RP_ID=<id>.ngrok-free.app` and restart. A new ngrok URL means registering again.

**Why** Tests mock the fingerprint check; only a real device proves the browser part.

**How to check** The page says "All set ✅" and the WhatsApp bot says the account is secured.

## 3. Production storage: Cloudflare R2 (before deploying)

**What to do** Create a PRIVATE R2 bucket and an API token limited to that bucket. On
Railway set `STORAGE_BACKEND=s3`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`.

**Why** With `ENV=production` the service refuses to start with local storage or a
non-https `PUBLIC_BASE_URL` (fail closed). Railway disks are not for sensitive files.

**How to check** The identity-api logs on Railway show the migrations and no start-up error.
