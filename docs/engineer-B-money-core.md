# Engineer B: Money Core (from scratch to deployment)

> **Read `00-START-HERE-shared-contracts.md` first.** You must know the contracts (section 6) and the Claude Code workflow (section 8).

## Your mission
You own **everything about money**:
1. The **ledger**: the single source of truth for balances
2. **Transfers**: a strict state machine that never pays twice
3. **Exchange rates and quotes**: fresh, with expiry
4. The **policy engine**: plain rule-based code that says yes/no
5. **Auto-exchange rules**: rate targets, recurring runs, freeze

**Your security promise:** *"Money can't move wrongly, and never twice."*

**You own:** `services/money/`, port **8002**, Postgres database `money`, Redis keys `rates:*`, `rules:*`, `locks:*`.
**You never touch:** WhatsApp, the LLM, passkeys, documents, C's tables.

> ⚠️ In this MVP, money moves only through a **MockProvider** (fake). Never connect a real payment provider until the "before real money" list in the handbook is done.

## Final folder structure
```
services/money/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py              ← FastAPI app + internal routes
│   ├── auth.py              ← X-Internal-Key dependency
│   ├── db.py                ← async engine + session
│   ├── models.py            ← SQLAlchemy tables
│   ├── ledger.py            ← post_entries, get_balance
│   ├── transfers.py         ← state machine
│   ├── providers/
│   │   ├── base.py          ← Provider interface
│   │   └── mock.py          ← MockProvider (random failures/timeouts)
│   ├── fx.py                ← rate fetcher + quotes
│   ├── policy.py            ← policy engine (pure functions)
│   ├── proposals.py         ← proposal → policy → quote → approval
│   ├── executor.py          ← executes approved transfers
│   ├── rules_engine.py      ← rate-target matcher, recurring cron
│   ├── events.py            ← publish to events:notify
│   ├── identity_client.py   ← calls C
│   └── jobs.py              ← background loops (rates, rules, executor)
├── alembic/
├── tests/
├── mock_server.py
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## STEP 0: Setup (30 min)

```powershell
cd student-money-agent\services
mkdir money
cd money
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 🤖 Prompt for Claude Code
```
I'm Engineer B. Work only inside services/money. Read CLAUDE.md and
docs/00-START-HERE-shared-contracts.md first.

Create:
1. requirements.txt: fastapi, uvicorn[standard], pydantic, pydantic-settings, python-dotenv,
   sqlalchemy[asyncio], asyncpg, alembic, redis, httpx, pytest, pytest-asyncio,
   fakeredis, ruff, aiosqlite, "-e ../../packages/contracts"
2. app/config.py (pydantic-settings): DATABASE_URL, REDIS_URL, INTERNAL_API_KEY,
   IDENTITY_URL, RATE_SOURCE_URL (optional), MAX_PER_TX_BDT (default 50000),
   MAX_PER_DAY_BDT (default 100000), QUOTE_TTL_SECONDS (60), RATE_STALE_SECONDS (120),
   FEE_PERCENT (default 0.5), FEE_MIN_BDT (default 20).
3. app/auth.py: FastAPI dependency require_internal_key that checks header
   X-Internal-Key with hmac.compare_digest, else 401.
4. app/db.py: async engine + async_sessionmaker + get_session dependency.
5. app/main.py with GET /health.
6. .env.example
Tell me the commands to install and run on port 8002.
```

### ✅ Verify
```powershell
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8002
```
http://127.0.0.1:8002/health → `{"status":"ok"}`

---

## DAY 1: Database + double-entry ledger

### Why double-entry?
Every money movement is written as **two rows that cancel out**. Money never appears from nowhere or vanishes. If the sum of all entries for a transfer isn't zero, something is wrong.

```
Convert 1000 BDT → IDR (rate 135, fee 20 BDT)
  user:BDT        -1000
  house:fx:BDT    +980
  house:fees:BDT  +20
  house:fx:IDR    -132300
  user:IDR        +132300
```

### Step 1.1: Tables
#### 🤖 Prompt for Claude Code
```
In services/money create app/models.py with SQLAlchemy 2.0 typed models
(UUID primary keys, created_at timestamps with timezone, Numeric(20,4) for money,
NEVER float):

- accounts(id, owner_type "user"|"house", owner_id UUID nullable, name, currency,
  unique(owner_type, owner_id, name, currency))
- ledger_entries(id, transfer_id, account_id FK, amount Numeric signed, currency,
  created_at). Insert-only.
- quotes(id, pair, rate, fee, amount_in, amount_out, expires_at, provider)
- transfers(id, user_id, kind "conversion", from_currency, to_currency, amount,
  quote_id FK nullable, mandate_id nullable, state, idempotency_key UNIQUE,
  payload_hash, approval_id nullable, provider_ref nullable, failure_reason nullable,
  created_at, updated_at)
- mandates(id, user_id, from_currency, to_currency, target_rate, amount_per_run,
  max_per_month, valid_until, state "pending_signature"|"active"|"paused"|"expired",
  signature_id nullable, payload_hash, idempotency_key UNIQUE, last_run_at nullable)
- recipients(id, user_id, provider_token, masked_label, cooling_until)
- freezes(user_id PK, frozen_at)

Set up Alembic (async) in services/money/alembic and create the first migration.
Then create a SECOND migration that makes ledger_entries append-only at the DB level:
a trigger that raises an exception on UPDATE or DELETE of ledger_entries.
```

#### ✅ Verify
```powershell
alembic upgrade head
docker compose exec postgres psql -U app -d money -c "\dt"
```
You should see all tables. Then try to break the ledger (this must fail):
```powershell
docker compose exec postgres psql -U app -d money -c "DELETE FROM ledger_entries;"
```
Expected: an error from the trigger. ✅

### Step 1.2: Ledger functions
#### 🤖 Prompt for Claude Code
```
Create app/ledger.py:
- get_or_create_account(session, owner_type, owner_id, name, currency)
- post_entries(session, transfer_id, entries: list[(account, Decimal amount)])
  Rules: at least 2 entries; per currency the amounts must sum to exactly 0, else raise
  LedgerImbalance; all in ONE database transaction.
- get_balance(session, user_id) -> dict[currency, Decimal]: sum of the user's accounts.
- A dev-only function seed_user_funds(session, user_id, amount, "BDT") that credits the
  user from house:funding (used for demos and tests; disabled when ENV=production).
Tests (use SQLite via aiosqlite for unit tests, or a test Postgres DB): balanced post
works, unbalanced raises, balance math correct, Decimal precision correct.
```

---

## DAY 2: Transfers + mock provider (no double payments!)

### The state machine
```
CREATED → QUOTED → PENDING_APPROVAL → APPROVED → SUBMITTED → SETTLED
                         │                │           └──→ FAILED
                         └→ EXPIRED       └→ (quote expired) → EXPIRED
```
Only these arrows are allowed. Anything else is a bug and must raise an error.

### Step 2.1: State machine + idempotency
#### 🤖 Prompt for Claude Code
```
Create app/transfers.py:
- ALLOWED = dict of state -> set of next states (as in the diagram above, include FAILED
  from SUBMITTED and EXPIRED from QUOTED/PENDING_APPROVAL/APPROVED).
- async advance(session, transfer_id, new_state, **fields): SELECT ... FOR UPDATE the row,
  check ALLOWED, update state + fields + updated_at. Illegal -> raise IllegalTransition.
- async create_transfer(session, proposal: TransferProposal) -> Transfer:
  if a transfer with the same idempotency_key exists, RETURN THE EXISTING ONE (no new row).
  Use INSERT ... ON CONFLICT DO NOTHING then SELECT, so two simultaneous calls make one row.
Tests: illegal transitions raise; same idempotency key twice -> same transfer id;
20 concurrent create calls with the same key -> exactly 1 row.
```

### Step 2.2: Provider interface + MockProvider
#### 🤖 Prompt for Claude Code
```
Create app/providers/base.py: an abstract Provider with async methods:
  quote(pair, amount) -> (rate, fee, amount_out)
  submit(idempotency_key, transfer details) -> provider_ref
  status(idempotency_key) -> "pending"|"settled"|"failed"|"unknown"

Create app/providers/mock.py MockProvider:
- Keeps its own in-memory (or Redis) record keyed by idempotency_key.
- submit with the SAME key twice returns the SAME provider_ref and never "executes" twice.
- Random behaviour controlled by env MOCK_FAILURE_RATE (default 0.1) and
  MOCK_TIMEOUT_RATE (default 0.1): sometimes raises ProviderTimeout AFTER recording the
  transfer (simulating "we don't know if it went through").
- Rates come from fx module (Day 3); for now a constant BDT_IDR = 135.20.

Create app/executor.py execute_transfer(transfer_id):
1. Load transfer; must be APPROVED; check freeze (Day 5) and quote not expired, else EXPIRED/fail.
2. advance to SUBMITTED, then provider.submit(idempotency_key).
3. On ProviderTimeout: DO NOT resubmit. Poll provider.status(key) up to 5 times with backoff.
   settled -> settle; failed -> FAILED; still unknown -> leave SUBMITTED and let a
   reconciliation job check later.
4. settle = post_entries (balanced, see example in this guide) + advance SETTLED +
   publish event transfer_settled. Failure -> FAILED + event transfer_failed.
Tests: with MOCK_TIMEOUT_RATE=1.0 the provider is called with submit exactly ONCE;
running execute_transfer twice on the same transfer settles only once; ledger balanced.
```

#### ✅ Verify
```powershell
pytest -q
```
The double-payment tests must pass. **This is the most important test in the whole project.**

---

## DAY 3: Rates, quotes, policy engine

### Step 3.1: Rates + quotes
#### 🤖 Prompt for Claude Code
```
Create app/fx.py:
- async fetch_rates(): get rates for BDT/IDR/USD pairs from RATE_SOURCE_URL if set
  (any free public FX API; parse defensively), otherwise from a MockRateSource whose
  rate can be changed via Redis key "rates:mock:BDT_IDR" (for demos).
  Store in Redis hash "rates:{pair}" with fields rate and fetched_at (ISO UTC).
  Rates are Decimals stored as strings.
- async get_rate(pair) -> RateOut; raises RateStale if older than RATE_STALE_SECONDS.
- async create_quote(session, pair, amount) -> Quote row: fee = max(FEE_MIN_BDT,
  amount * FEE_PERCENT/100), amount_out = (amount - fee) * rate rounded DOWN to 2dp,
  expires_at = now + QUOTE_TTL_SECONDS.
- Add GET /internal/rates/{pair} (protected) returning RateOut.
- A loop in app/jobs.py: every 60s fetch_rates().
Tests: rounding is down, fee minimum applies, stale rate raises.
```

### Step 3.2: Policy engine (the heart of safety)
#### 🤖 Prompt for Claude Code
```
Create app/policy.py with PURE functions (no DB, no network; all inputs passed in):

@dataclass PolicyContext: user_frozen: bool, amount_bdt: Decimal, spent_today_bdt: Decimal,
  spent_this_month_for_mandate: Decimal | None, mandate_max_per_month: Decimal | None,
  rate_age_seconds: int, quote_expires_in_seconds: int | None,
  recipient_cooling_active: bool, mandate_active: bool | None,
  mandate_valid_until_passed: bool | None, currency_pair_supported: bool

def evaluate(ctx) -> Decision(allowed: bool, reasons: list[str])
Rules (ALL must pass; collect every failing reason):
- not frozen
- pair supported
- amount > 0 and <= MAX_PER_TX_BDT
- spent_today + amount <= MAX_PER_DAY_BDT
- rate_age <= RATE_STALE_SECONDS
- if quote given: expires_in > 5 seconds
- recipient not in cooling period
- if mandate: active, not past valid_until, monthly spend + amount <= max_per_month
Unknown/None where a value is required -> DENY (fail closed).

Write tests/test_policy.py with one test per rule (allowed and denied cases),
plus a test that a context with every value at its limit is allowed and limit+0.01 is denied.
Target 100% coverage of policy.py (add pytest-cov and show the coverage).
```

#### ✅ Verify
```powershell
pip install pytest-cov
pytest -q --cov=app.policy --cov-report=term-missing
```
`policy.py` should be at **100%**.

---

## DAY 4: Proposals → approval → execution

### The flow you're building
```
A calls POST /internal/proposals/transfer
   → create_transfer (idempotent) → quote → policy.evaluate
   → denied?  return {status:"rejected", reason}
   → allowed: payload_hash(summary) → call C POST /internal/approvals → PENDING_APPROVAL
             → publish event approval_needed {url} → return {status:"pending_approval", url}
User approves on C's page with fingerprint
   → C calls POST /internal/approvals/confirmed
   → check payload_hash matches, state PENDING_APPROVAL, quote not expired
   → APPROVED → execute_transfer (background) → SETTLED → event transfer_settled
```

### 🤖 Prompt for Claude Code
```
1. app/identity_client.py: create_approval(ApprovalCreate) -> ApprovalCreated and
   verify_signature(VerifySignatureIn) -> bool, with X-Internal-Key, timeout 10s.
   Also tests/mocks/mock_identity.py: create_approval returns
   url http://localhost:8003/approve/<id>; verify_signature returns valid true.
2. app/events.py: publish(type, user_id, data) -> XADD events:notify data=<NotifyEvent json>.
   Never include phone numbers or names.
3. app/proposals.py + routes in main.py (all protected by require_internal_key):
   - POST /internal/proposals/transfer (TransferProposal -> ProposalResult) as in the flow.
     The summary sent to C must include: from/to currency, amount, rate, fee,
     amount_out, expires_at, provider name. payload_hash = contracts.hashing.payload_hash(summary).
     Store payload_hash on the transfer.
   - POST /internal/proposals/rule (RuleProposal -> ProposalResult): create mandate in
     pending_signature (idempotent), summary of the rule, approval kind "rule".
   - POST /internal/approvals/confirmed (ApprovalConfirmed):
     look up the subject; payload_hash must match exactly (compare_digest) else 409;
     state must be PENDING_APPROVAL (transfer) or pending_signature (mandate) else 409
     (this makes confirmations single-use);
     transfer -> APPROVED then schedule execute_transfer;
     mandate -> store signature_id, state active, index it in the rules engine (Day 5).
   - GET /internal/balance/{user_id}, GET /internal/rules/{user_id}
4. A dev-only route POST /dev/seed/{user_id} (only when ENV != production) that seeds funds.
Tests for: rejected proposal (over limit), happy path to SETTLED with mocks,
tampered payload_hash -> 409, confirming twice -> 409 the second time,
expired quote at confirmation -> EXPIRED and no provider call.
```

#### ✅ Verify
With `tests/mocks/mock_identity.py` running on 8003:
1. Open http://127.0.0.1:8002/docs
2. `POST /dev/seed/{user_id}` with a random UUID
3. `POST /internal/proposals/transfer` (add the `X-Internal-Key` header) → `pending_approval`
4. Simulate C: `POST /internal/approvals/confirmed` with the same payload_hash → wait 2s → `GET /internal/balance` shows IDR

---

## DAY 5: Auto-exchange rules + freeze + reconciliation

### Step 5.1: Rate-target matcher
**Idea:** don't loop over all users. Keep rules in a Redis **sorted set** scored by target rate, then ask Redis: "which rules have target ≤ current rate?"

#### 🤖 Prompt for Claude Code
```
Create app/rules_engine.py:
- index_rule(mandate): ZADD "rules:{pair}" target_rate mandate_id
- unindex_rule(mandate_id, pair): ZREM
- async on_rate(pair, rate): ids = ZRANGEBYSCORE rules:{pair} -inf rate.
  For each id: acquire Redis lock "locks:mandate:{id}:{YYYY-MM-DD}" with SET NX EX 86400
  (max one run per mandate per day); if lock taken skip.
  Then run_mandate(id).
- async run_mandate(id):
  1. load mandate; must be active and not past valid_until (else expire + unindex)
  2. identity_client.verify_signature(user_id, signature_id, payload_hash) must be True,
     else pause mandate + event + stop (FAIL CLOSED)
  3. build a TransferProposal with idempotency_key f"mandate:{id}:{date}",
     quote, policy.evaluate WITH mandate fields; denied -> event rule_skipped reason
  4. create transfer with mandate_id, go straight to APPROVED (the user pre-approved by
     signing the rule), execute_transfer, publish rule_triggered.
- In jobs.py, after each fetch_rates call on_rate for each pair, but ONLY if the rate is
  fresh (stale-rate guard: if stale, skip all rules and log a warning).
- On startup: rebuild the sorted sets from all active mandates in Postgres.
Tests: rule fires when rate crosses target; does not fire below target; fires at most
once per day; invalid signature pauses the rule; stale rate fires nothing.
```

### Step 5.2: Freeze (kill switch)
#### 🤖 Prompt for Claude Code
```
- POST /internal/users/{user_id}/freeze-money: insert into freezes; pause all user's
  mandates (state paused, unindex); publish event "frozen".
- execute_transfer must check freezes RIGHT BEFORE provider.submit (not only earlier).
- policy context user_frozen comes from the freezes table.
- Global kill switch: Redis key "money:global_freeze" = "1" blocks ALL submits.
Tests: freeze during a pending transfer prevents submit; global freeze blocks everything.
```

### Step 5.3: Reconciliation
#### 🤖 Prompt for Claude Code
```
In jobs.py add reconcile loop every 5 minutes:
- transfers stuck in SUBMITTED > 2 minutes: provider.status(key) -> settle or fail.
- for every SETTLED transfer, ledger entries per currency must sum to 0; if not,
  log CRITICAL and set Redis "money:global_freeze" = "1" (fail closed).
Tests for both.
```

### Step 5.4: Background runner
#### 🤖 Prompt for Claude Code
```
Make jobs.py runnable as `python -m app.jobs`: one asyncio process running the rates
loop (60s), rules check after each rate fetch, executor queue consumer, and reconcile (5 min).
Each loop catches and logs exceptions and keeps running.
```

#### ✅ Verify: demo the auto-exchange
```powershell
# terminal 1
uvicorn app.main:app --port 8002
# terminal 2
python -m app.jobs
# terminal 3: create + confirm a rule with target 136 via /docs, then raise the mock rate:
docker compose exec redis redis-cli SET rates:mock:BDT_IDR 136.5
```
Within about 60 seconds the rule fires: a new SETTLED transfer, a balance change, and a `rule_triggered` event in the `events:notify` stream.

---

## DAY 6: Integration (with A and C)
1. Stop your mocks. Set `IDENTITY_URL=http://localhost:8003` to point at the real C.
2. Run the API + `python -m app.jobs`.
3. Run the handbook demo script together. Your parts: approval → settle → receipt; auto-rule; freeze.

---

## DAY 7: Deployment (Railway)

### 🤖 Prompt for Claude Code
```
Create services/money/Dockerfile (python:3.12-slim, non-root, build context repo root,
install packages/contracts + requirements). CMD runs "alembic upgrade head" then uvicorn
on ${PORT:-8002}. Add .dockerignore. Make sure /dev/* routes and seed functions are
disabled when ENV=production (add a test).
```

On Railway (with Engineer C):
1. Add a **Postgres** plugin, and create a database for money (or use a separate Postgres instance for money, which is the better isolation).
2. Service `money-api`: **no public domain** (internal only, reachable on the private network).
3. Service `money-jobs`: start command `python -m app.jobs`.
4. Variables: `ENV=production`, `DATABASE_URL` (Railway Postgres, using the `postgresql+asyncpg://` prefix), `REDIS_URL`, `INTERNAL_API_KEY`, `IDENTITY_URL` (private URL of identity), **small limits** (`MAX_PER_TX_BDT=5000`).
5. Deploy → check the logs for "alembic upgrade" success.

### ✅ Verify
From the deployed messaging bot, "convert 1000 taka" works end to end.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `asyncpg` connection refused | Is `docker compose up -d` running? Does `DATABASE_URL` use `postgresql+asyncpg://`? |
| Alembic "target database is not up to date" | `alembic upgrade head` |
| Decimal errors like `0.30000000004` | You used float somewhere. Use `Decimal("0.3")` and `Numeric` columns |
| Double settlement in tests | You're resubmitting after a timeout. Use `status()` instead |
| Rule never fires | Is the rate fresh? Is the rule active and indexed (`ZRANGE rules:BDT_IDR 0 -1 WITHSCORES`)? Is the daily lock already taken? |

## ✅ Your done checklist
- [ ] Ledger: balanced posts only, DB trigger blocks UPDATE/DELETE
- [ ] Transfer state machine + idempotency (20 concurrent → 1 row)
- [ ] Timeout → status check, never resubmit (test passes)
- [ ] Quotes expire; rounding down; fees
- [ ] Policy engine 100% test coverage
- [ ] Proposal → approval → confirmed (single-use, hash match) → settled
- [ ] Rate-target rules: fire once per day, verify signature every run, stale-rate guard
- [ ] Freeze checked right before submit; global kill switch
- [ ] Reconciliation job
- [ ] Deployed (internal-only API) with small limits
