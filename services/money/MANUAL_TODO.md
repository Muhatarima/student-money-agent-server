# Things only you can do (money service)

## 1. Make sure `ENV` is set in `services\money\.env` (now required)

**What to do**
1. Open `services\money\.env` in your editor.
2. Make sure it has the line `ENV=dev` (it's in `.env.example`, so it's probably there already).
3. On Railway, set `ENV=production` for `money-api` and `money-jobs`.

**Why** `ENV` no longer defaults to `dev`. Before, a forgotten `ENV` in production would turn
on `/dev/seed`, which creates money. Now the app refuses to start without it.

**Affects** `.env` / Railway variables, `app/config.py`.

**How to check** From `services\money`: `.venv\Scripts\python -c "from app.config import settings; print(settings.env)"` prints `dev`.

## 2. Apply the new migration to your local `money` database

**What to do** From `services\money` (Docker running): `.venv\Scripts\alembic upgrade head`

**Why** Migration `0004` adds an index for the reconcile job. The tests use `money_test` and
migrate it themselves; your dev database `money` needs this once.

**How to check** `.venv\Scripts\alembic current` prints `0004 (head)`.
