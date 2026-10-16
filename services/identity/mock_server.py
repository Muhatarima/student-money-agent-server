"""FAKE Identity service for teammates: every endpoint from the contract, with fake data.
Run from services/identity:  uvicorn mock_server:app --port 8003
Needs only INTERNAL_API_KEY in the environment (same value as your service uses).
"""

import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

from contracts.models import (
    ApprovalCreate,
    ApprovalCreated,
    LinkTokenOut,
    UserOut,
    VerifySignatureIn,
    VerifySignatureOut,
)
from fastapi import Depends, FastAPI, Header, HTTPException

BASE = "http://localhost:8003"


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    expected = os.environ.get("INTERNAL_API_KEY", "").encode()
    if not expected or not hmac.compare_digest(x_internal_key.encode(), expected):
        raise HTTPException(401, "Invalid internal key")  # empty key blocks everyone


app = FastAPI(title="Mock Identity", dependencies=[Depends(require_internal_key)])


@app.get("/internal/users/by-phone/{phone_hash}")
async def by_phone(phone_hash: str) -> UserOut:
    user_id = uuid.uuid5(uuid.NAMESPACE_OID, phone_hash)  # same hash -> same user
    return UserOut(user_id=user_id, linked=True, frozen=False)


@app.post("/internal/link-tokens")
async def link_token(body: dict) -> LinkTokenOut:
    return LinkTokenOut(
        url=f"{BASE}/link?t=demo", expires_at=datetime.now(UTC) + timedelta(minutes=10)
    )


@app.get("/internal/users/{user_id}/wa-target")
async def wa_target(user_id: uuid.UUID) -> dict:
    return {"wa_id": "8801700000000"}


@app.post("/internal/users/{user_id}/freeze")
async def freeze(user_id: uuid.UUID) -> dict:
    return {"frozen": True}


@app.post("/internal/upload-links")
async def upload_link(body: dict) -> dict:
    return {"url": f"{BASE}/upload/demo?t=demo"}


@app.post("/internal/approvals")
async def approvals(body: ApprovalCreate) -> ApprovalCreated:
    approval_id = uuid.uuid4()
    return ApprovalCreated(approval_id=approval_id, url=f"{BASE}/approve/{approval_id}?t=demo")


@app.post("/internal/verify-signature")
async def verify_signature(body: VerifySignatureIn) -> VerifySignatureOut:
    return VerifySignatureOut(valid=True)
