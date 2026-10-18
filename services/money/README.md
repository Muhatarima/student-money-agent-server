# Money (Engineer B): ledger, transfers, rates, auto-rules

Everything about money: a double-entry ledger (insert-only), a transfer state machine that
never pays twice, quotes, the policy engine (`app/policy.py`), auto-exchange rules and freeze.
In this MVP money moves **only** through `MockProvider` (fake).

Port **8002**, internal only (no public URL in production). Postgres database `money`,
Redis keys `rates:*`, `rules:*`, `locks:*`, `money:global_freeze`.

## The two processes
| Process | Command | Job |
|---|---|---|
| api | `uvicorn app.main:app --port 8002` | Internal API: rates, balance, proposals, approvals, freeze |
| jobs | `python -m app.jobs` | Fetches rates every 60 s and runs matching rules; reconciles every 5 min |

## Run it on your laptop
From the repo root, start Postgres + Redis once (`docker compose up -d`) and create the
databases once:
```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE money_test;"
```
Then:
```powershell
cd services\money
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # set INTERNAL_API_KEY: see MANUAL_TODO.md at the repo root
alembic upgrade head        # creates/updates the tables
uvicorn app.main:app --port 8002
```
and `python -m app.jobs` in a second terminal. API docs: http://127.0.0.1:8002/docs

## Settings (`.env`)
Every setting is listed in `.env.example`.
- `ENV` is required: `dev` (turns on `POST /dev/seed/{user_id}`, test money) or `production`.
- Limits: `MAX_PER_TX_BDT`, `MAX_PER_DAY_BDT`. Keep them small in production.
- `RATE_SOURCE_URL` empty = mock rates. Change one for a demo:
  `docker compose exec redis redis-cli SET rates:mock:BDT_IDR 136.5`
- `MOCK_FAILURE_RATE` / `MOCK_TIMEOUT_RATE`: how often the fake provider fails (0.0-1.0).

## Tests
```powershell
pytest -q                                            # needs Docker Postgres (money_test) + Redis
pytest -q --cov=app.policy --cov-report=term-missing # policy.py must stay at 100%
ruff check .
```

## Docker
`docker build -f services/money/Dockerfile -t money .` (from the repo root). The api runs
`alembic upgrade head` before starting. Same image for jobs: start command `python -m app.jobs`.
