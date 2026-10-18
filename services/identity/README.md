# Identity (Engineer C): users, passkeys, approval pages, document vault

Makes sure **only the real user can approve**: WhatsApp numbers are linked with a passkey
(fingerprint / Face ID), and every approval is signed with a challenge built from exactly
what the page showed. Also: the encrypted document vault and a hash-chained audit log.

Port **8003**. This is the only service with public web pages. Postgres database `identity`.

## The two processes
| Process | Command | Job |
|---|---|---|
| api | `uvicorn app.main:app --port 8003` | Internal API + pages: `/link`, `/approve/...`, `/upload/...` |
| jobs | `python -m app.jobs` | Every 10 min: deletes documents past their date, expired links and approvals |

## Run it on your laptop
From the repo root, start Postgres + Redis once (`docker compose up -d`) and create the
databases once:
```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity_test;"
```
Then:
```powershell
cd services\identity
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # make the 3 keys: see MANUAL_TODO.md at the repo root
alembic upgrade head
uvicorn app.main:app --port 8003
```
and `python -m app.jobs` in a second terminal. Passkeys work on `http://localhost` without
HTTPS. For a phone, use ngrok (see MANUAL_TODO.md).

## Settings (`.env`)
Every setting is listed in `.env.example`.
- `PUBLIC_BASE_URL` must be exactly the address in the browser (scheme + host + port), and
  `RP_ID` the host only. Passkeys are tied to that domain.
- `FERNET_KEY`, `VAULT_MASTER_KEY`, `SESSION_SECRET`: three different keys. Losing
  `VAULT_MASTER_KEY` makes all stored documents unreadable.
- `STORAGE_BACKEND=local` (folder `vault_local/`, dev only) or `s3` (Cloudflare R2).
- With `ENV=production` it refuses to start unless `PUBLIC_BASE_URL` is https and storage is s3.

## Tests
```powershell
pytest -q          # needs Docker Postgres (identity_test); Redis and WebAuthn are faked
ruff check .
```

## Docker
`docker build -f services/identity/Dockerfile -t identity .` (from the repo root). The api
runs `alembic upgrade head` before starting. Same image for jobs: `python -m app.jobs`.
