# Finish progress (docs/FINISH_PROMPT.md)

Per-service history from the earlier session: `services/messaging/PROGRESS.md`.

## Current phase
Phase 1 (quick fixes) is **done** on branch `a-day1-autonomous`.

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

## Test results
- messaging: `pytest -q` → 78 passed; `ruff check .` clean
- money: `pytest -q` → 277 passed (needs Docker Postgres running); `ruff check .` clean

## Blocked
None.

## Next step
Phase 2: `git switch build/money`, review `services/money` against
`docs/engineer-B-money-core.md` + `CLAUDE.md`, write `services/money/REVIEW.md`, and fix
real bugs test-first.
