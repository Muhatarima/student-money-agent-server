# Student Money Agent

A WhatsApp assistant for international students.  
Students can convert money (e.g. BDT → IDR), set auto-exchange rules, and manage documents — all through WhatsApp, secured by passkeys (fingerprint / Face ID).

## Architecture

```
Student (WhatsApp)
      │
      ▼
┌─────────────────────┐  proposal  ┌─────────────────────┐  create approval  ┌───────────────────────┐
│ A: Messaging & AI   │──────────▶ │ B: Money Core        │ ────────────────▶ │ C: Identity & Web     │
│ Port 8001           │ ◀──────── │ Port 8002            │ ◀──────────────── │ Port 8003             │
│ webhook, queue, LLM │  events   │ ledger, FX, rules    │  "confirmed"      │ passkeys, approvals   │
└─────────────────────┘            └─────────────────────┘                   └───────────────────────┘
```

| Service | Owner | Port | Owns |
|---|---|---|---|
| `services/messaging` | Engineer A | 8001 | WhatsApp, queues, AI agent |
| `services/money` | Engineer B | 8002 | Ledger, transfers, FX rates, mandates |
| `services/identity` | Engineer C | 8003 | Users, passkeys, approvals, vault |
| `packages/contracts` | All | — | Shared Pydantic models & contracts |

## Quickstart (local dev)

### 1. Prerequisites

- Docker Desktop
- Python 3.12
- ngrok (for passkey / WhatsApp testing from phone)

### 2. Start Postgres + Redis

```powershell
docker compose up -d
```

Create the two databases (only once):

```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity;"
```

### 3. Run each service

Open a PowerShell terminal per service:

```powershell
# Terminal 1 — Identity (Engineer C)
cd services\identity
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # fill real values
alembic upgrade head
uvicorn app.main:app --reload --port 8003

# Terminal 2 — Money (Engineer B)
cd services\money
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8002

# Terminal 3 — Messaging API (Engineer A)
cd services\messaging
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8001

# Terminal 4 — Messaging Worker (Engineer A)
cd services\messaging; .venv\Scripts\Activate.ps1
python -m app.worker

# Terminal 5 — Money Jobs / Rule Engine (Engineer B)
cd services\money; .venv\Scripts\Activate.ps1
python -m app.jobs

# Terminal 6 — Identity Jobs / Vault Purge (Engineer C)
cd services\identity; .venv\Scripts\Activate.ps1
python -m app.jobs
```

### 4. Verify everything is up

```
http://localhost:8001/health  →  {"status":"ok"}
http://localhost:8002/health  →  {"status":"ok"}
http://localhost:8003/health  →  {"status":"ok"}
```

## Generate shared secrets

```powershell
# INTERNAL_API_KEY and PHONE_HASH_KEY (one person generates, shares via password manager)
python -c "import secrets; print(secrets.token_hex(32))"

# Fernet keys (FERNET_KEY and VAULT_MASTER_KEY — generate two)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Production Deployment (Railway)

The system deploys as 7 services from the single repository:
- `messaging-api` (public domain for Meta WhatsApp webhook)
- `messaging-worker` (`python -m app.worker`)
- `messaging-notifier` (`python -m app.notifier`)
- `money-api` (private network only, no public domain)
- `money-jobs` (`python -m app.jobs`)
- `identity-api` (public domain for passkey linking, approvals, and vault)
- `identity-jobs` (`python -m app.jobs`)

See [`services/identity/README.md`](services/identity/README.md) and [`docs/engineer-C-identity-web-vault.md`](docs/engineer-C-identity-web-vault.md) for full Railway provisioning steps and environment configuration.

## Guides

| Guide | Audience |
|---|---|
| [`docs/00-START-HERE-shared-contracts.md`](docs/00-START-HERE-shared-contracts.md) | Everyone — read first |
| [`docs/engineer-A-messaging-ai.md`](docs/engineer-A-messaging-ai.md) | Engineer A |
| [`docs/engineer-B-money-core.md`](docs/engineer-B-money-core.md) | Engineer B |
| [`docs/engineer-C-identity-web-vault.md`](docs/engineer-C-identity-web-vault.md) | Engineer C |

## Running tests

```powershell
# From inside a service folder (with .venv active):
pytest -q

# Contracts package:
cd packages\contracts
pip install -e . pytest
pytest -q
```

## CI

GitHub Actions runs on every push:
- Lint (`ruff check .`) for each service
- `pytest -q` for each service + the contracts package
- Postgres 16 and Redis 7 are available as service containers

See [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

