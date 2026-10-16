# Finish progress (docs/FINISH_PROMPT.md)

Per-service history from the earlier session: `services/messaging/PROGRESS.md`.

## Current phase
Phases 1, 2 and 3 are **done**. Next: Phase 4 (new branch `build/identity` from `build/money`).

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

## Test results
- messaging (`a-day1-autonomous`): `pytest -q` → 167 passed; `ruff check .` clean
- money (`build/money`): `pytest -q` → 285 passed; `ruff check .` clean; `policy.py` 100%
  coverage; `alembic check` (on money_test): no differences. Needs Docker Postgres + Redis.

## Blocked
None.

## Notes for later phases
- Phase 4: identity must refuse every request when its configured `INTERNAL_API_KEY` is
  empty (like money and the messaging mocks), and use `hide_input_in_errors=True`.
- Phase 4: identity publishes `account_linked` on `events:notify` (the notifier already
  handles it).

## Next step
Phase 4: `git switch -c build/identity build/money`, then follow
`docs/engineer-C-identity-web-vault.md` from Step 0.
