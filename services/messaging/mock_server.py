"""Fake WhatsApp for teammates: catches messages instead of sending them to Meta.
Run from services/messaging:  uvicorn mock_server:app --port 8009

POST /fake-whatsapp/send  takes the same JSON body as Meta's /messages endpoint
GET  /fake-whatsapp/sent  shows what was "sent" (kept in memory, never printed or logged,
                          because messages can contain approval links)
With WA_FAKE_SEND=true, app/whatsapp.py never calls Meta. Set WA_FAKE_SEND_URL to
http://localhost:8009/fake-whatsapp/send to see the worker's and notifier's messages here.
"""

from fastapi import FastAPI

from app.security import mask_phone

app = FastAPI(title="Mock WhatsApp")
sent: list[dict] = []


@app.post("/fake-whatsapp/send")
async def send(message: dict) -> dict:
    sent.append(message)
    return {"messages": [{"id": f"wamid.fake{len(sent)}"}]}  # same shape Meta answers with


@app.get("/fake-whatsapp/sent")
async def list_sent() -> list[dict]:
    # Phones are masked even here
    return [m | {"to": mask_phone(str(m.get("to", "")))} for m in sent]
