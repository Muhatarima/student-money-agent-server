# Finish progress (docs/FINISH_PROMPT.md)

Per-service history from the earlier session: `services/messaging/PROGRESS.md`.

## Current phase
**All 6 phases are done.** Everything is on `build/integration` (pushed). Nothing was
deployed and nothing was committed to `main`. What's left is human-only: `MANUAL_TODO.md`.

## Final summary
- **What exists:** three services (messaging, money, identity) built to the handbook and
  the three engineer guides, sharing `packages/contracts` (never changed). 7 processes from
  3 Docker images.
- **Proof it works:** every service's `pytest -q` passes, `ruff check .` is clean,
  `policy.py` has 100% coverage, and `tests/e2e` plays the handbook demo on all 7 real
  processes in Docker (link -> rate -> conversion approved -> receipt -> rule -> auto-run ->
  FREEZE with exactly one message -> everything blocked -> upload -> purge).
- **Safety properties tested:** the AI only reads, proposes or freezes; money can't move
  twice (idempotency keys, never resubmit after a timeout); insert-only ledger and
  append-only hash-chained audit log; approvals signed over exactly what the page showed;
  fail closed on unknown/invalid input, an empty internal key, missing `ENV`, or an
  unverifiable rule signature; secrets and phones masked in logs; documents encrypted
  with their own key and purged after 7 days.
- **Read these next:** `MANUAL_TODO.md` (your ordered checklist), `DEPLOY.md` (Railway +
  R2 recipe), each `services/*/README.md`, and "Decisions to review" in each phase below.
- **CI:** `.github/workflows/ci.yml` runs ruff + pytest for the 3 services and the
  contracts on every push and pull request (see "CI" below).
- **Known limit (not a bug in the demo path):** money moves only through the fake
  `MockProvider` (its records are per process, see Phase 5).

## Phase 1: quick fixes (branch `a-day1-autonomous`)

| # | Commit | What |
|---|---|---|
| 1 | `9d02cd7` A: whatsapp.py shows Meta's error code on 4xx; scripts/send_test_message.py | The two uncommitted changes, committed as they were |
| 3 | `8a31653` A-fix3 | Tests: a 4xx error shows Meta's status, code and message, has no full phone or token, and is never retried. The two old 4xx tests now expect `RuntimeError` |
| 2 | `0121aa9` A-fix2 | `hide_input_in_errors=True` in the messaging and money config. New `tests/test_config.py` in each service: the error for a missing setting still names the field but shows no value from the environment (each test fails without the fix) |

Item 3 was done before item 2 because the item 1 commit broke two old whatsapp tests,
and every commit has to pass its tests.

Note: `hide_input_in_errors` only cleans the printed message (`str(error)`, which is what a
crash traceback shows). `error.json()` / `error.errors()` still contain the input, and
that is pydantic's built-in behaviour. Never log those for settings errors.

Identity has no config yet. It gets the same setting when it is built (Phase 4).

## Phase 2: review of Engineer B (branch `build/money`, pushed)

Full findings: `services/money/REVIEW.md` (on `build/money`). Each fix has a test that failed first.

| Commit | Fix |
|---|---|
| `1630297` B-fix | `hide_input_in_errors=True` + test (same as Phase 1, now also on `build/money`) |
| `aff76db` B-review | An empty `INTERNAL_API_KEY` now blocks every request (before, an empty header got in) |
| `5dc99dc` B-review | `ENV` is required (before, a missing `ENV` meant `dev`, which turns on `/dev/seed`) |
| `8572615` B-review | Daily/monthly limits only count transfers that can still move money |
| `17ec9fb` B-review | Per-user advisory lock: proposals at the same moment can't get past the daily limit |
| `a3207ba` B-review | An approved transfer blocked before submit now sends `transfer_failed` |
| `e593266` B-review | A rule `valid_until` without a timezone is rejected (before: 500 error) |
| `e3d03b9` B-review | Partial index for APPROVED transfers (migration 0004) |
| `6266fb4` B-review | `REVIEW.md` + `services/money/MANUAL_TODO.md` (set `ENV=dev`, run `alembic upgrade head`) |

## Phase 3: finish Engineer A (branch `a-day1-autonomous`, pushed)

| Commit | What |
|---|---|
| `972343f` A-step2.3 | `app/notifier.py`: `events:notify`, group `messaging`, XACK only after the send works, retry, `queue:notify_dead` after 3 failures. Handles approval_needed, transfer_settled, transfer_failed, rule_triggered, **rule_skipped, rule_paused**, account_linked, frozen; reason codes shown in plain words. `scripts/push_fake_event.py` |
| `1b00119` A-step2.2 | `app/handlers.py` linking flow (link button for new/unlinked users), frozen reply; the webhook job now carries `type` + `button_id`; the worker calls `handlers.handle_message` |
| `d10b75e` A-step3.2 | `app/agent.py`: Anthropic tool use (`claude-opus-5-5`, effort low, server-side refusal fallback). Tools: get_rate, get_balance, list_rules, propose_transfer, propose_rule, freeze_account only. Pydantic checks every input; user_id always ours; masked text; 3 tool rounds max; 900-char replies; chat history in `chat:{user_id}` (10 turns, 24h). Prompt-injection test |
| `92ed9d4` A-step3.3 | `FREEZE` (any case) and the menu buttons (rates / rules / freeze) work without the LLM; no AI answer → 3-button menu |
| `48a4a8a` A-day4 | Rate limit 20/min per user (one polite reply), 2000-char limit, files and `UPLOAD` → secure upload link (files never downloaded) |
| `f365003` A-day4 | `app/logs.py`: one logging setup with a mask filter (phones, bearer tokens, `token=`/`key=` values, URL paths) on our logs and uvicorn's; all `print`s are now log calls |
| `2332ce7` A-day4 | `/health` pings Redis (503 if down); webhook bodies over 1 MB → 413 |
| `b870265` A-day5/7 | `Dockerfile` (non-root; one image for api / worker / notifier; build + `/health` checked in Docker), `mock_server.py` (fake WhatsApp), `ANTHROPIC_API_KEY` + `LLM_MODEL` in `.env.example` |
| `eb281ab` A | The mocks refuse every request when the configured internal key is empty |

Decisions to review:
- The model never sees the approval URL: proposal tools return only status/reason, and
  the link button comes from the `approval_needed` event (notifier). A tricked model
  can't rewrite the link.
- No `ANTHROPIC_API_KEY` → the AI is simply off and users get the menu (no crash). See
  `services/messaging/MANUAL_TODO.md` item 6.
- `get_rate` for IDR_BDT / USD_BDT is computed as 1 / BDT_IDR (Money only quotes from BDT).
- FREEZE sends an instant confirmation, and the `frozen` event from Money sends a second
  one through the notifier.
- README for messaging is Phase 6.

## Phase 4: build Engineer C (branch `build/identity`, from `build/money`, pushed)

| Commit | What |
|---|---|
| `7880d4f` C-step0 | Config with `hide_input_in_errors=True` and required `ENV`; an empty `INTERNAL_API_KEY` blocks every request; db, `/health` |
| `eac28a3` C-step1.1 | Models + Alembic 0001 (unique token hashes / phone hash / credential id, partial index `documents(delete_after) WHERE deleted_at IS NULL`); 0002 append-only trigger on `audit_events`; hash-chained `audit()` + `verify_chain()` |
| `91b5dae` C-step1.2 | `crypto.py` (Fernet wa_id, token hashes); by-phone, link-tokens (5/h per phone), wa-target, freeze (expires pending approvals, audit, `frozen` event) |
| `bd312bb` C-step2 | Link page, passkey registration (challenge single-use in Redis, Origin + JSON-only checks), `start_auth` / `finish_auth`; `passkey.js` with `@simplewebauthn/browser@13.2.0` + SRI hash |
| `e821a3e` C-step3 | Approvals: page shows exactly the summary + countdown; challenge = sha256(payload_hash + nonce); summary hash re-checked (a tampered summary can't be approved); single-use; frozen / expired / other user's passkey refused; confirms to Money; cancel |
| `b5b5a08` C-step4 | `/internal/verify-signature` re-verifies the stored assertion every time, fail closed, audited; `mock_server.py` |
| `4996695` C-step5.1 | Vault: upload links, passkey-gated page, signed strict HttpOnly cookie + CSRF, magic-byte check (pdf/jpeg/png), 10 MB, envelope encryption, local/S3 storage; `python -m app.jobs` purge |
| `4b51acd` C-step5.2 | Security headers (CSP, HSTS in production, nosniff, no-referrer, DENY, no-store), 11 MB request cap, verify rate limits (10/min per IP and per link), token-masking logs, `/health` checks DB + Redis, production refuses http / local storage |
| `58d33b0` C-step7.1 | Dockerfile (non-root, migrations on start, proxy headers; built and smoke-tested), `MANUAL_TODO.md`. The Docker smoke test found that the log filter broke uvicorn's access log; fixed + test |

Decisions to review:
- No `PHONE_HASH_KEY` in identity: it only ever receives hashes from Messaging.
- Tests mock WebAuthn with a fake that still checks challenge, origin and which key signed.
- The venv is Python 3.14 (no 3.12 on this machine); the Docker image uses 3.12.
- The README for identity is in Phase 6.

## Phase 5: integration (branch `build/integration`, pushed)

`build/integration` = `build/identity` + `a-day1-autonomous` (merge `51610ac`; `build/money`
is already inside both). After the merge: no conflict markers, `packages/contracts` identical
to `build/money`, and all three suites passed before any change.

| Commit | What |
|---|---|
| `1dbee8e` I-fix | Messaging's log filter kept the uvicorn access-log crash Identity had: it now masks each argument and keeps the args tuple. Test with uvicorn's real `AccessFormatter` |
| `9252a37` I-fix | **One FREEZE = one WhatsApp message.** The notifier acks `frozen` events (Identity and Money each publish one) without sending, because the FREEZE handler already confirmed. **Fail-open bug fixed:** if Identity's freeze worked but Money's failed, the user was "frozen" in Identity, so a second FREEZE only got "your account is frozen" and Money was never frozen (rules kept running). FREEZE now always re-runs both freezes (safe to repeat) |
| `5ef5696` I | `WA_FAKE_SEND_URL`: in fake mode, also POST each message to `mock_server.py`, so other processes (worker, notifier) can be seen. In `.env.example` |
| `c60f7ad` I | Identity `purge(session, now=...)` + `purge_once()`, so the e2e can purge "as if 8 days later". The Docker image creates `vault_local/` so a shared dev volume is owned by the app user |
| (this commit) | `tests/e2e`: the handbook demo on the real services in Docker |

### tests/e2e (how it works)
- `docker-compose.yml` starts all 7 processes (messaging api/worker/notifier, money api/jobs,
  identity api/jobs) + Postgres + Redis + fake WhatsApp (`mock_server.py`) + `fake_llm.py`.
  Host ports 18001-18009, so it never clashes with the dev stack. Test-only values only.
- Fakes only at the edges: WhatsApp (`WA_FAKE_SEND=true`), the LLM (`ANTHROPIC_BASE_URL`
  points at the scripted `fake_llm.py`; the agent code is real), MockProvider (failure and
  timeout rates 0), and the fingerprint sensor: `passkey.py` is a software passkey that makes
  real WebAuthn data, so Identity's real py_webauthn checks run. There is **no test bypass in
  any service**.
- `test_demo.py`: link -> rate question -> one-off conversion approved -> receipt + balances
  -> rule signed -> mock rate raised -> auto-conversion -> FREEZE (exactly one message, then
  nothing for 5 s) -> WhatsApp, Money proposals, rules and upload links are all blocked ->
  document upload -> purge (the file is gone).
- Run: `cd tests\e2e`, make a venv, `pip install -r requirements.txt`, `pytest -q`
  (about 2 min; first build longer). `E2E_KEEP=1` leaves the containers up for logs.

Decisions to review:
- The demo uploads with a **second student**: Identity refuses upload links for frozen users
  (C's design, fail closed), so the frozen student can't upload. The e2e checks that refusal.
- The rule step waits for Money's 60 s rate loop (no test-only shortcut in the jobs).

Findings not fixed (no service bug in the demo path):
- MockProvider keeps its records in memory per process. The API and the jobs process each
  have their own, so `reconcile` (jobs) can't see a transfer that timed out in the API: it
  would stay SUBMITTED. It can never pay twice. A real provider keeps this state on its side,
  so this only matters for the mock (that's why the e2e sets the timeout rate to 0).
- Docker Desktop on this laptop sometimes pauses its whole VM (seen: 222 s with no health
  checks at all). Two early runs failed on that. The test now uses 30 s timeouts and keeps
  polling after a timeout; a pause longer than a step's wait can still fail it. Just rerun.

## Phase 6: deployment prep (branch `build/integration`, pushed)

| Commit | What |
|---|---|
| `ffe4d70` D | Dockerfiles take `HOST` (default `0.0.0.0`, so Docker and the e2e are unchanged). On Railway `HOST=::` for `money-api` and `identity-api`, because Railway's private network uses IPv6 (smoke-tested in Docker: `::` answers on IPv6) |
| `aa7c106` D | `README.md` in each service: processes, local run, settings, tests, Docker |
| `e1cac21` D | Root `DEPLOY.md`: Railway (2 Postgres + Redis, 7 services, Dockerfile paths, start commands, healthchecks, 2 public domains only, a variables block per service with `${{...}}` references and the `postgresql+asyncpg://` URL), Cloudflare R2 (private bucket + bucket-only token), deploy order, checks, Meta webhook, production checklist, rollback, emergency global freeze, troubleshooting |
| `ff9bd57` D | One root `MANUAL_TODO.md`: ordered checklist (A: laptop, B: online, C: before real money), each with do / why / where / check. The three service `MANUAL_TODO.md` files are merged into it and removed |

The 7 processes come from 3 Dockerfiles, one image per service, with a different start command
for each process (see the table at the top of `DEPLOY.md`). Each service's `.env.example`
was checked against its `config.py`: every setting is listed.

Decisions to review:
- Production `MOCK_FAILURE_RATE=0` and `MOCK_TIMEOUT_RATE=0` in `DEPLOY.md`, so the demo on
  production is predictable. Raise them to rehearse failures.
- Production limits suggested: `MAX_PER_TX_BDT=5000`, `MAX_PER_DAY_BDT=10000`.

## CI (branch `build/integration`, pushed)

| Commit | What |
|---|---|
| `3264bb6` CI | `.github/workflows/ci.yml` on every push and pull request. Job `contracts`: ruff + `pytest tests/contracts`. Job `service` (messaging, money, identity in parallel, with Postgres 16 + Redis 7 and the two `_test` databases): `pip install -r requirements.txt`, `ruff check .`, `pytest -q`; money also fails if `policy.py` drops below 100% coverage |

- New `tests/contracts/test_contracts.py` (9 tests: hash ignores key order, a known-answer
  hash that pins the algorithm, one digit changes the hash, Decimal amounts, bad proposals
  rejected, event round trip). It lives outside `packages/contracts`, so the package is
  unchanged.
- Checked before pushing: every job's commands in `python:3.12-slim` on Linux, on a clean
  `git archive` of the commit (no `.env`, no venvs), with fresh Postgres + Redis: all green.
  (I also checked that no suite secretly relies on a local `.env`.)
- `tests/e2e` is not in CI (several minutes, builds 3 images): run it locally before merging.

Optional contract cleanup (style only, needs "contract change approved"): `contracts/hashing.py`
has `import hashlib, json` on one line and `contracts/events.py` has a comment longer than 88
characters. CI checks the contracts only for real errors (syntax, undefined/unused names) so
these don't fail it.

## Test results (on `build/integration`, final)
- messaging: `pytest -q` → 171 passed; `ruff check .` clean
- money: `pytest -q` → 285 passed; `ruff check .` clean; `policy.py` 100% coverage.
  Needs Docker Postgres (`money_test`) + Redis.
- identity: `pytest -q` → 123 passed; `ruff check .` clean. Needs Docker Postgres (`identity_test`).
- tests/e2e: `pytest -q` → 1 passed in 2 min 6 s on freshly built images; `ruff check .` clean.
  Needs Docker Desktop.

## Blocked
None.

## Next step
Human-only: work through `MANUAL_TODO.md` from A1 (B1 now includes checking the green CI
run and making it required for `main`). Code work, if wanted later: a real licensed provider
(handbook section 11).
