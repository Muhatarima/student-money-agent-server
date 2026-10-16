# Messaging (Engineer A): WhatsApp + queue + AI agent

Receives WhatsApp messages, answers them, and sends notifications from Money and Identity.
**The AI can never move money**: its tools only read data, create proposals, or freeze.

Port **8001**. Uses Redis only (keys `wa:*`, `queue:*`, `chat:*`). No database.

## The three processes
| Process | Command | Job |
|---|---|---|
| api | `uvicorn app.main:app --port 8001` | `/webhook`: checks Meta's signature, blocks duplicates, queues the message |
| worker | `python -m app.worker` | Answers queued messages (linking, FREEZE, menu, AI agent) |
| notifier | `python -m app.notifier` | Turns `events:notify` from Money/Identity into WhatsApp messages |

## Run it on your laptop
From the repo root, start Postgres + Redis once: `docker compose up -d`. Then:
```powershell
cd services\messaging
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then fill it in: see MANUAL_TODO.md at the repo root
```
Run each process in its own terminal (venv active). Money (8002) and Identity (8003) must be
running too, or use the fakes: `uvicorn tests.mocks.mock_money:app --port 8002` and
`uvicorn tests.mocks.mock_identity:app --port 8003`.

## Settings (`.env`)
Every setting is listed in `.env.example`. The important switches:
- `WA_FAKE_SEND=true`: nothing is sent to Meta (messages are only logged, masked).
  `WA_FAKE_SEND_URL=http://localhost:8009/fake-whatsapp/send` also shows them in
  `mock_server.py` (`uvicorn mock_server:app --port 8009`, then open `/fake-whatsapp/sent`).
- `ANTHROPIC_API_KEY` empty: the AI is off and users get a 3-button menu. FREEZE still works.

## Tests
```powershell
pytest -q          # needs nothing running: Redis is faked, Meta and the LLM are mocked
ruff check .
```
The whole-system test is in `tests/e2e` at the repo root.

## Handy scripts
- `python -m scripts.push_fake_event transfer_settled`: push a fake event to watch the notifier.
- `python scripts/send_test_message.py`: send one real WhatsApp message (needs a real token).

## Docker
`docker build -f services/messaging/Dockerfile -t messaging .` (from the repo root).
One image for all three processes: change the start command. See `DEPLOY.md`.
