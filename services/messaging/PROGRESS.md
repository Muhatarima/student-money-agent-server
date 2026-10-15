# Messaging (Engineer A): autonomous session progress

Branch: `a-day1-autonomous` (main untouched). Date: 2026-10-15.

## Finished (one commit each, tests passing at every commit)

| Commit | What |
|---|---|
| `A-prep` 9477685 | `app/config.py` (pydantic-settings, `SecretStr`) + `app/redis_client.py` (shared client, protocol=2, socket_timeout 30). **These did not exist in the repo yet**, so I created them; `main.py` and `worker.py` now use them. Same behaviour. |
| `A-item1` a4cd5aa | pytest tests: handshake ok / 403, valid signature 200, fake / missing 401, tampered 401, 3 sends → 1 new, enqueue failure → dedupe key forgotten |
| `A-item2` bc78647 | `app/queue.py` (queue names + `enqueue_incoming`) and `app/dedupe.py` (`is_duplicate`, `forget`), shared by main and worker |
| `A-item3` 306ee8d | Worker: crash recovery on start (processing → incoming, order kept); failure → retried; 3rd failure → `queue:dead`; unreadable job → `queue:dead`. Also: worker **no longer prints message text** (it did before) |
| `A-item4` 4dbca9a | `app/whatsapp.py`: `send_text`, `send_buttons`, `send_link_button`; `WA_FAKE_SEND=true` records to `fake_outbox` and prints only type + masked phone; retries 5xx/timeouts ×3, never 4xx; no token → refuses to send |
| `A-item5` 611740e | `app/security.py`: `mask_phone`, `hash_phone` (HMAC, digits only), `mask_pii` (13–19 digits → `<CARD_REMOVED>`, never in mapping; emails, passport-like IDs, 9+ digit runs → `<PII_n>`). All `****1234` masking now uses `mask_phone` |
| `A-item6` a728e4d | `app/clients.py` (`MoneyClient`, `IdentityClient`, `ServiceUnavailable`) + `tests/mocks/mock_money.py` (8002) and `mock_identity.py` (8003), shapes from `packages/contracts`, both check `X-Internal-Key` |

## Test results
`pytest -q` → **76 passed**. `ruff check .` → clean.

## Half-done / decisions to review
- **New required settings**: `PHONE_HASH_KEY`, `INTERNAL_API_KEY`, `MONEY_URL`, `IDENTITY_URL`.
  The app won't start until your `.env` has them (see `MANUAL_TODO.md` items 1, 4, 5).
  There is no `.env` in `services/messaging` yet.
- `mask_pii`: a phone number with country code (e.g. +8801700001234, 13 digits) is treated
  like a card and removed (not stored). Chosen on purpose: removing is the safe side.
  Two numbers written next to each other ("1000 2000 3000") can merge into one token.
- Worker retries a failed job right away (up to 3 tries total); there is no delay
  between tries yet.
- Fake send mode prints only the message type and masked phone, not the text, because
  replies can contain approval/link URLs.
- `scripts/test_webhook.py` and `scripts/test_dedupe.py` are unchanged (still read
  `WA_APP_SECRET` straight from `.env`).
- The pushed branch also contains the money-service commits (`B-*`) from local `main`,
  because `main` had not been pushed yet.

## Next 3 steps
1. Do `MANUAL_TODO.md` items 1–3 (create `.env`, Meta token, ngrok), then the Day 1
   finish line: `app/handlers.py` replying "Hi 👋 I received: <text>" via `send_text`.
2. Step 2.2 linking flow in `handlers.py`: `hash_phone` → `IdentityClient.get_user_by_phone`
   → link button / frozen message / "Linked ✅", tested with the mocks.
3. Step 2.3 `app/notifier.py`: consume `events:notify` (consumer group `messaging`),
   send the WhatsApp templates, XACK only after the send succeeds.
