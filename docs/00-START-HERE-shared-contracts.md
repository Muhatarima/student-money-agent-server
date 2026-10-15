# Student Money Agent: Team Handbook (read this first)

> **Everyone reads this file first.** Then each engineer opens their own guide:
> - `engineer-A-messaging-ai.md`: WhatsApp + queue + AI agent
> - `engineer-B-money-core.md`: ledger, transfers, exchange rates, auto-exchange
> - `engineer-C-identity-web-vault.md`: users, passkeys, approval pages, document vault, deployment

---

## 1. What we are building (one paragraph)

A WhatsApp assistant for international students. A student chats normally ("what's the BDT→IDR rate?", "convert 10,000 taka when the rate hits 135"). An AI understands the message, but **the AI can never move money**. It can only create a *proposal*. Strict, rule-based code checks the proposal, gets a live quote, and sends the student a link to a web page. There the student approves with **fingerprint / Face ID (a passkey)**. Only then does money move, and in this MVP it only moves through a **mock / sandbox provider**. Documents (passport, visa) are uploaded on the web page, encrypted, and auto-deleted.

```
 Student (WhatsApp)
      │ message
      ▼
┌───────────────────┐   proposal    ┌───────────────────┐  create approval  ┌────────────────────┐
│ A: Messaging & AI │ ────────────▶ │ B: Money Core     │ ────────────────▶ │ C: Identity & Web  │
│ webhook, queue,   │ ◀──────────── │ policy, quotes,   │ ◀──────────────── │ passkeys, approval │
│ worker, LLM agent │  notify events│ ledger, auto-rules│  "confirmed"      │ pages, vault, audit│
└───────────────────┘               └───────────────────┘                   └────────────────────┘
        ▲                                                                           │
        └──────────── user opens approval link in browser, uses fingerprint ◀───────┘
```

---

## 2. Who owns what

| | Engineer A | Engineer B | Engineer C |
|---|---|---|---|
| Service | `services/messaging` | `services/money` | `services/identity` (+ `web` pages inside it) |
| Local port | **8001** | **8002** | **8003** |
| Owns data | Redis keys `wa:*`, `queue:*`, `chat:*` | Postgres tables `quotes, transfers, ledger_entries, mandates, recipients, freezes` + Redis `rates:*`, `rules:*` | Postgres tables `users, passkeys, sessions, link_tokens, approvals, signatures, documents, audit_events` |
| Security promise | "The AI can't do damage" | "Money can't move wrongly or twice" | "Only the real user can approve" |
| Also owns | — | — | Repo setup, CI, docker-compose, deployment |

**Golden rule:** never read or write another engineer's tables. If you need their data, **call their API**.

---

## 3. The stack (same for everyone)

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | One language for the whole team |
| Web framework | **FastAPI** + **Uvicorn** | Fast, async, auto docs at `/docs` |
| Data models | **Pydantic v2** | Strict input/output checking |
| Database | **PostgreSQL 16** | Permanent data (money!) |
| DB access | **SQLAlchemy 2.0 (async)** + **asyncpg** + **Alembic** (migrations) | Standard, safe from SQL injection |
| Cache / queue | **Redis 7** (`redis` library, `protocol=2`) | Dedupe, queues, rates, events |
| HTTP client | **httpx** | Calls between services and to Meta |
| AI | **Anthropic Python SDK** (Claude, tool use) | Structured tool calls |
| Passkeys | **py_webauthn** (server) + **@simplewebauthn/browser** (page script) | Standard WebAuthn |
| Web pages | **Jinja2** HTML templates served by FastAPI | No separate frontend needed for MVP |
| Encryption | **cryptography** (Fernet) | Document encryption |
| Tests | **pytest** + **pytest-asyncio** | |
| Lint/format | **ruff** | |
| Local infra | **Docker Desktop** + `docker-compose.yml` (Postgres + Redis) | Same setup on every laptop |
| CI | **GitHub Actions** | Runs lint + tests on every push |
| Deploy | **Railway** (services + Postgres + Redis) | Easiest for beginners |
| Local tunnel | **ngrok** | So Meta can reach your laptop |

> If Docker Desktop won't install on your laptop: use **Neon** (free Postgres) + **Upstash** (free Redis) and put their URLs in `.env`. For Upstash, the Redis URL must start with `rediss://` and the code must use `protocol=2`.

---

## 4. Repo layout (Engineer C creates this on Day 0)

```
student-money-agent/
├── CLAUDE.md                     ← rules Claude Code must follow (section 8)
├── docker-compose.yml            ← Postgres + Redis for local dev
├── .gitignore
├── .github/workflows/ci.yml
├── packages/
│   └── contracts/                ← SHARED Pydantic models (everyone imports this)
│       ├── pyproject.toml
│       └── contracts/
│           ├── __init__.py
│           ├── models.py
│           └── events.py
├── services/
│   ├── messaging/   (A)  app/ tests/ mock_server.py  requirements.txt  Dockerfile  .env.example
│   ├── money/       (B)  app/ tests/ mock_server.py  alembic/ requirements.txt  Dockerfile  .env.example
│   └── identity/    (C)  app/ tests/ mock_server.py  alembic/ templates/ static/ requirements.txt  Dockerfile  .env.example
└── docs/                         ← these 4 guides live here
```

Each service is **its own FastAPI app with its own virtual environment** (`.venv` inside the service folder).

---

## 5. Shared environment variables

Every service has a `.env` (never committed) and a `.env.example` (committed, no real values).

| Variable | Used by | What it is |
|---|---|---|
| `INTERNAL_API_KEY` | A, B, C | Shared secret sent as `X-Internal-Key` header on every internal call |
| `PHONE_HASH_KEY` | A, C | Secret used to HMAC-hash phone numbers (never store raw phones) |
| `REDIS_URL` | A, B, C | e.g. `redis://localhost:6379/0` |
| `DATABASE_URL` | B, C | e.g. `postgresql+asyncpg://app:app@localhost:5432/money` (B) / `.../identity` (C) |
| `MONEY_URL` | A, C | `http://localhost:8002` |
| `IDENTITY_URL` | A, B | `http://localhost:8003` |
| `PUBLIC_BASE_URL` | C | Public URL of identity pages, e.g. `http://localhost:8003` or the Railway URL |

Generate secrets with:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
**One person generates `INTERNAL_API_KEY` and `PHONE_HASH_KEY` and shares them privately** (not in git, not in chat groups). Use a password manager.

---

## 6. THE CONTRACTS (agreed on Day 0, changed only if all 3 agree)

### 6.1 Shared models: `packages/contracts/contracts/models.py`

```python
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field


class Currency(str, Enum):
    BDT = "BDT"
    IDR = "IDR"
    USD = "USD"


class TransferProposal(BaseModel):
    user_id: UUID
    from_currency: Currency
    to_currency: Currency
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)  # in from_currency
    recipient_id: UUID | None = None   # None = user's own wallet
    idempotency_key: str = Field(min_length=8, max_length=100)


class RuleProposal(BaseModel):
    user_id: UUID
    from_currency: Currency
    to_currency: Currency
    target_rate: Decimal = Field(gt=0)
    amount_per_run: Decimal = Field(gt=0)
    max_per_month: Decimal = Field(gt=0)
    valid_until: datetime
    idempotency_key: str = Field(min_length=8, max_length=100)


class ProposalResult(BaseModel):
    status: str                      # "pending_approval" | "rejected"
    reason: str | None = None        # why rejected (safe to show user)
    approval_url: str | None = None
    subject_id: UUID | None = None   # transfer_id or mandate_id


class RateOut(BaseModel):
    pair: str                        # "BDT_IDR"
    rate: Decimal
    fetched_at: datetime


class BalanceOut(BaseModel):
    user_id: UUID
    balances: dict[str, Decimal]     # {"BDT": ..., "IDR": ...}


class ApprovalCreate(BaseModel):
    user_id: UUID
    kind: str                        # "transfer" | "rule"
    subject_id: UUID                 # transfer_id or mandate_id (owned by B)
    payload_hash: str                # sha256 hex of canonical JSON of what is approved
    summary: dict                    # human-readable fields to show on the page
    expires_at: datetime


class ApprovalCreated(BaseModel):
    approval_id: UUID
    url: str


class ApprovalConfirmed(BaseModel):
    approval_id: UUID
    subject_id: UUID
    payload_hash: str
    signature_id: UUID               # stored passkey assertion (owned by C)


class VerifySignatureIn(BaseModel):
    user_id: UUID
    signature_id: UUID
    payload_hash: str


class VerifySignatureOut(BaseModel):
    valid: bool


class UserOut(BaseModel):
    user_id: UUID
    linked: bool
    frozen: bool


class LinkTokenOut(BaseModel):
    url: str
    expires_at: datetime
```

### 6.2 Internal API endpoints

All require header `X-Internal-Key: <INTERNAL_API_KEY>` (compare with `hmac.compare_digest`). Missing or wrong key → `401`.

| Method & path | Owner | Caller | Request → Response |
|---|---|---|---|
| `GET /internal/users/by-phone/{phone_hash}` | C | A | → `UserOut` or `404` |
| `POST /internal/link-tokens` body `{"phone_hash": str, "wa_id": str}` | C | A | → `LinkTokenOut` (C stores `wa_id` encrypted) |
| `GET /internal/users/{user_id}/wa-target` | C | A | → `{"wa_id": str}` |
| `POST /internal/upload-links` body `{"user_id": UUID}` | C | A | → `{"url": str}` |
| `POST /internal/users/{user_id}/freeze` | C | A | → `{"frozen": true}` (blocks new approvals) |
| `GET /internal/rates/{pair}` | B | A | → `RateOut` |
| `GET /internal/balance/{user_id}` | B | A | → `BalanceOut` |
| `POST /internal/proposals/transfer` | B | A | `TransferProposal` → `ProposalResult` |
| `POST /internal/proposals/rule` | B | A | `RuleProposal` → `ProposalResult` |
| `GET /internal/rules/{user_id}` | B | A | → list of the user's rules (id, pair, target_rate, status) |
| `POST /internal/users/{user_id}/freeze-money` | B | A | → `{"frozen": true}` (pauses rules, blocks submits) |
| `POST /internal/approvals` | C | B | `ApprovalCreate` → `ApprovalCreated` |
| `POST /internal/approvals/confirmed` | B | C | `ApprovalConfirmed` → `{"ok": true}` |
| `POST /internal/verify-signature` | C | B | `VerifySignatureIn` → `VerifySignatureOut` |

### 6.3 Events: `packages/contracts/contracts/events.py`

B and C **publish** to Redis stream `events:notify` with `XADD`. A **consumes** with consumer group `messaging`, then sends WhatsApp messages. Each entry has one field, `data`, holding JSON:

```python
from pydantic import BaseModel
from uuid import UUID

class NotifyEvent(BaseModel):
    type: str          # "approval_needed" | "transfer_settled" | "transfer_failed"
                       # | "rule_triggered" | "account_linked" | "frozen"
    user_id: UUID
    data: dict         # e.g. {"url": ...} or {"amount": "10000", "rate": "135.2", "fee": "45"}
```

Events **never** contain full phone numbers, passport data or account numbers. A looks up where to send the message by calling C (`GET /internal/users/{user_id}/wa-target`). C stores the WhatsApp number **encrypted** and returns it only on this internal endpoint.

### 6.4 Canonical JSON + payload hash (B and C must do this IDENTICALLY)

```python
import hashlib, json

def canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def payload_hash(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()
```
Put this in `packages/contracts/contracts/hashing.py` so both use the same code.

---

## 7. Day 0 checklist (all three together, ~half a day)

- [ ] C creates the GitHub repo, adds A and B as collaborators
- [ ] C creates the folder layout (section 4), `CLAUDE.md`, `.gitignore`, `docker-compose.yml`, CI file
- [ ] All three write `packages/contracts` together (copy section 6 exactly)
- [ ] Everyone runs `docker compose up -d` and sees Postgres + Redis running
- [ ] Everyone installs Claude Code and opens the repo with it
- [ ] Secrets generated and shared privately
- [ ] Agree on daily 15-min sync time

### `docker-compose.yml` (local dev only)
```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: postgres
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
  redis:
    image: redis:7
    ports: ["6379:6379"]
volumes:
  pgdata:
```
After it starts, create the two databases once:
```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE identity;"
```

### `.gitignore`
```
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
vault_local/
```

---

## 8. How to use Claude Code (for beginners)

1. Install Claude Code by following the official quickstart at **docs.claude.com** (Claude Code → Quickstart).
2. Open PowerShell in the repo folder and type `claude`.
3. Each step in your guide has a box called **🤖 Prompt for Claude Code**. Copy the whole box and paste it into Claude Code.
4. Claude Code will create and edit files and ask permission to run commands. Read what it wants to do and approve.
5. After each step, run the **✅ Verify** commands yourself. **Do not move to the next step until Verify passes.**
6. If something fails, paste the full error into Claude Code and say: *"This failed during step X. Fix it without changing the contracts."*
7. Commit after every passing step:
   ```powershell
   git add .
   git commit -m "A-step-3: dedupe works"
   git push
   ```

### `CLAUDE.md` (put in repo root; Claude Code reads it automatically)
```markdown
# Rules for this repo (Claude Code must follow)

## Project
WhatsApp money assistant for students. Monorepo: services/messaging (A), services/money (B),
services/identity (C), shared models in packages/contracts. Python 3.12, FastAPI, SQLAlchemy 2 async,
Alembic, Redis (protocol=2), pytest, ruff. Windows + PowerShell dev machines.

## Never break these
- NEVER change packages/contracts without the human saying "contract change approved".
- NEVER read/write another service's database tables. Call its HTTP API.
- NEVER log full phone numbers, names, passport/document numbers, tokens, or secrets. Mask: ****1234.
- NEVER store card numbers, CVV, PIN, OTP, or bank passwords anywhere.
- NEVER let the LLM call anything that moves money. LLM tools may only read data or create proposals.
- Money amounts use Decimal, never float.
- Every money-moving operation needs an idempotency key and must be safe to retry.
- Ledger rows are insert-only. No UPDATE or DELETE on ledger_entries.
- Compare secrets with hmac.compare_digest.
- Secrets come from environment variables (.env), never hard-coded.
- Every internal endpoint checks the X-Internal-Key header.
- When unsure about security, choose the option that blocks the action ("fail closed").

## Working style
- Small steps. After each change, run the tests: `pytest -q` in the service folder.
- Write a test for every policy rule and every security check.
- Explain what you changed in simple English (the developer is a beginner).
```

---

## 9. Daily rhythm

| Time | What |
|---|---|
| Start of day | 15-min sync: done / doing / blocked / contract questions |
| During day | Work through your guide step by step. Commit after each ✅ |
| End of day | Push, make sure CI is green, update the checklist at the bottom of your guide |

---

## 10. Days 6–7: integration and deployment (all together)

| When | Who | Task |
|---|---|---|
| Day 6 morning | All | Run all 3 services locally against real Postgres/Redis (no mocks). Set `MONEY_URL`, `IDENTITY_URL` to real ports |
| Day 6 afternoon | All | **End-to-end test**: WhatsApp "convert 1000 BDT to IDR" → approval link → fingerprint → mock provider settles → receipt in WhatsApp |
| Day 6 afternoon | All | Auto-rule test: create rule → change mock rate → auto-convert → receipt; then "freeze" → rule blocked |
| Day 7 | C (lead) | Deploy all 3 to Railway (see C's guide, section "Deployment") |
| Day 7 | A | Point the Meta webhook to the Railway messaging URL |
| Day 7 | B | Production config: stale-rate guard on, limits small |
| Day 7 | All | Run the demo script on the deployed version |

### Demo script (the definition of "MVP done")
1. Message the bot "hi" → receive link → register fingerprint
2. Ask "what's the BDT to IDR rate?" → bot answers
3. "Convert 1000 taka to rupiah" → approval link → page shows rate, fee, countdown → fingerprint → receipt
4. "Auto-convert 500 taka every time rate is above X" → approve rule with fingerprint
5. Nudge mock rate above X → automatic conversion + receipt
6. "Freeze" → everything paused, confirmed in WhatsApp
7. Upload passport on web page → shows auto-delete date

---

## 11. Before REAL money (not part of the 7 days)

1. Partner with a **licensed** payment/FX provider (they hold funds and do identity checks)
2. Legal advice on **Bangladesh Bank foreign-exchange rules** for your exact money flow
3. Move durable workflows to **Temporal**, encryption keys to **AWS KMS**
4. Independent **penetration test**
5. Closed beta with tiny limits

---

## 12. Glossary

| Word | Meaning |
|---|---|
| Webhook | A URL that another service (Meta) calls when something happens |
| HMAC | A signature made with a secret key. Proves a message is genuine and unchanged |
| Idempotency key | A unique ID for a request, so doing it twice has the same effect as once |
| Ledger | The record of all money movements; double-entry = every movement has a debit and a credit |
| Passkey / WebAuthn | Login and approval using fingerprint or Face ID instead of a password |
| Proposal | What the AI suggests. It does nothing until the policy engine and user approve |
| Policy engine | Plain rule-based code that says yes/no to a proposal (limits, freeze, expiry...) |
| Mandate / rule | A standing instruction, signed once by the user, that runs automatically within limits |
| Mock | A fake version of a service, used for testing before the real one exists |
