"""Fake Identity service (Engineer C) with made-up data.
Run from services/messaging:  uvicorn tests.mocks.mock_identity:app --port 8003

Every phone_hash is a linked user, except UNKNOWN_HASH (-> 404, a new user).
"""

import uuid
from datetime import UTC, datetime, timedelta

from contracts.models import LinkTokenOut, UserOut
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from tests.mocks.auth import require_internal_key

app = FastAPI(title="Mock Identity", dependencies=[Depends(require_internal_key)])

UNKNOWN_HASH = "0" * 64
frozen: set[uuid.UUID] = set()  # users frozen while the mock runs


class LinkTokenIn(BaseModel):
    phone_hash: str
    wa_id: str


class UploadLinkIn(BaseModel):
    user_id: uuid.UUID


def user_id_for(phone_hash: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_OID, phone_hash)  # same hash -> same user


@app.get("/internal/users/by-phone/{phone_hash}")
async def user_by_phone(phone_hash: str) -> UserOut:
    if phone_hash == UNKNOWN_HASH:
        raise HTTPException(404, "not found")
    user_id = user_id_for(phone_hash)
    return UserOut(user_id=user_id, linked=True, frozen=user_id in frozen)


@app.post("/internal/link-tokens")
async def link_token(body: LinkTokenIn) -> LinkTokenOut:
    return LinkTokenOut(
        url="http://localhost:8003/link/demo", expires_at=datetime.now(UTC) + timedelta(minutes=15)
    )


@app.get("/internal/users/{user_id}/wa-target")
async def wa_target(user_id: uuid.UUID) -> dict:
    return {"wa_id": "8801700001234"}


@app.post("/internal/upload-links")
async def upload_link(body: UploadLinkIn) -> dict:
    return {"url": "http://localhost:8003/upload/demo"}


@app.post("/internal/users/{user_id}/freeze")
async def freeze(user_id: uuid.UUID) -> dict:
    frozen.add(user_id)
    return {"frozen": True}
