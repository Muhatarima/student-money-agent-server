# Review of services/money (Engineer B)

Reviewed on branch `build/money` against `docs/engineer-B-money-core.md`, the handbook
and `CLAUDE.md` (2026-10-16). Every fixed bug got a test that failed before the fix.

**Overall:** solid. The most important parts are done well: double payments can't happen
(timeout → `status()`, never resubmit), the ledger is insert-only with a DB trigger, the
transfer state machine is strict, idempotency holds under 20 concurrent calls, `policy.py`
has 100% coverage, and the checks right before submit (freeze, global freeze, quote, funds)
are in the right place.

Tests: 277 before → **285 passed** after. `ruff check .` clean. `alembic check`: models
match the migrations.

## Fixed

| # | Severity | Area | Problem | Fix (commit) |
|---|---|---|---|---|
| 1 | High | Security | `INTERNAL_API_KEY=` (empty) in the env meant a request with **no** key header passed (`compare_digest("", "")` is true). | `auth.py`: an empty configured key blocks everyone. (`aff76db`) |
| 2 | Medium | Security | `ENV` defaulted to `"dev"`. A production deploy that forgot `ENV` would turn on `/dev/seed`, which creates money. | `ENV` is now required; the app refuses to start without it. (`5dc99dc`) |
| 3 | Medium | Money correctness | The daily and monthly limits counted transfers that can never move money: `CREATED` rows left by early rejections (stale rate, amount too small, pair not supported) and approvals whose quote had expired. Example: a 50,000 BDT proposal rejected for "rate unavailable" still used up half of the day's limit. | One helper, `can_still_move_money()`, used by both limit queries. (`8572615`) |
| 4 | Medium | Money correctness | Race: proposals arriving at the same moment each saw "spent 0" and all passed. 3 × 40,000 at once were all approved against a 100,000 daily limit (reproduced in a test). | `build_context` takes a per-user Postgres advisory lock (`pg_advisory_xact_lock`) that is held until the transfer's new state is committed. (`17ec9fb`) |
| 5 | Medium | Money / UX | A transfer the user had approved, but that was blocked right before submit (frozen, global freeze, quote expired, insufficient funds), became `EXPIRED` with **no message** to the user. | Executor publishes `transfer_failed` with the reason. (`a3207ba`) |
| 6 | Low | Input checks | A rule with a `valid_until` that has no timezone (e.g. `2099-01-01T00:00:00`) crashed with a 500 error. | Rejected with reason `valid_until_needs_timezone`. (`e593266`) |
| 7 | Low | DB indexes | Reconcile looks up `APPROVED` rows every 5 minutes with no index (`SUBMITTED` already had one). | Partial index `ix_transfers_approved_updated_at`, migration `0004`. (`e3d03b9`) |
| — | Low | Security | A settings validation error printed the other settings' values (passwords inside URLs, keys). | `hide_input_in_errors=True` + test. (`1630297`) |

## Not fixed (on purpose): known limits and notes

| Severity | Area | Note |
|---|---|---|
| Medium | Mock only | `MockProvider` keeps its records in memory, one copy per process. The API process and `python -m app.jobs` each have their own. If the API crashes while a transfer is `SUBMITTED`, reconcile (in the jobs process) asks its own mock, gets `"unknown"`, and the transfer stays `SUBMITTED` (safe, but stuck). A real provider keeps its records on its side, so this goes away. Not fixed: it only affects the fake provider, and fixing it would mean rewriting the mock and its tests. |
| Low | Behaviour | If C (identity) is down when a rule runs, `verify_signature` fails closed and the rule is **paused**. Nothing un-pauses it, so the user has to make a new rule. This matches the guide ("fail closed"). |
| Low | Contract note | Events `rule_paused` and `rule_skipped` are published but aren't in the comment list in `contracts/events.py`. `type` is a free string, so this is not a contract break. Engineer A's notifier must handle them (Phase 3). |
| Low | Behaviour | A retried proposal (same idempotency key) returns its current status without `approval_url`. The user already got the link through the `approval_needed` event. |
| Low | Behaviour | "Today" for the daily limit is UTC midnight (06:00 in Bangladesh). |
| Low | Behaviour | A rule approved after the user froze becomes `active`, but every run is still refused by the policy (`account_frozen`), and the rule is reported as `rule_skipped`. |
| Low | Guide deviation | No separate executor queue consumer in `jobs.py`. Approved transfers run as a FastAPI background task, and reconcile picks up any `APPROVED` transfer left for over 1 minute. Same result, less code. |
| Low | Guide deviation | `mock_server.py` from the guide's folder list doesn't exist. Engineer A already has `tests/mocks/mock_money.py`, and Phase 5 uses the real service. |
| Low | Clarity | `GET /internal/rules/{user_id}` selects whole mandate rows to return 4 fields. It's fine at this size. |

## Checked and fine
- Decimal everywhere for money; `Numeric(20,4)` money, `Numeric(20,8)` rates; floats only for
  Redis sorted-set scores, with an exact Decimal re-check (`rate < mandate.target_rate`).
- Fee rounds UP, `amount_out` rounds DOWN, the fee minimum only applies to BDT.
- Ledger: at least 2 entries, sums to 0 per currency, refuses amounts with more than 4 decimal places.
- The payload hash is compared with `hmac.compare_digest`; confirmation is single-use (state check under `FOR UPDATE`).
- Events contain no phone numbers or names.
- Indexes: unique on both idempotency keys, ledger `(account_id) INCLUDE amount`,
  `(user_id, created_at)` for the daily limit, partial `(mandate_id, created_at)` for the monthly limit.
