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
