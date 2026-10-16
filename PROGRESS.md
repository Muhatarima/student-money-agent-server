# Finish progress (docs/FINISH_PROMPT.md)

Per-service history from the earlier session: `services/messaging/PROGRESS.md`.

## Current phase
Phase 1 and Phase 2 are **done**. Next: Phase 3 (branch `a-day1-autonomous`).

This file lives only on `a-day1-autonomous` (so Phase 5's merge doesn't conflict).

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

## Test results
- messaging (`a-day1-autonomous`): `pytest -q` → 78 passed; `ruff check .` clean
- money (`build/money`): `pytest -q` → 285 passed; `ruff check .` clean; `policy.py` 100%
  coverage; `alembic check` (on money_test): no differences. Needs Docker Postgres + Redis.

## Blocked
None.

## Notes for later phases
- Phase 3 notifier: handle the `rule_paused` and `rule_skipped` events from money as well.
- Phase 3/4: every service that checks `X-Internal-Key` (identity, the messaging mocks)
  must also refuse when the configured key is empty, like the money fix.

## Next step
Phase 3 on `a-day1-autonomous`: `app/notifier.py` (consume `events:notify`, group
`messaging`, XACK only after the send works), then the linking flow + frozen handling in
`handlers.py`.
