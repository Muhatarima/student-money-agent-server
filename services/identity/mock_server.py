"""mock_server.py

Canonical fake Identity service implementing all contract endpoints for Engineer A and B.
Run with: uvicorn mock_server:app --port 8003
"""

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
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

app = FastAPI(title="Identity Service Mock", version="0.1.0")

STATIC_USER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
EXPECTED_INTERNAL_KEY = "test-internal-key"


def check_internal_key(x_internal_key: str | None = Header(None, alias="X-Internal-Key")) -> None:
    # Accept any key in mock or check standard key
    if not x_internal_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Internal-Key header",
        )


class LinkTokenRequest(BaseModel):
    phone_hash: str
    wa_id: str


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}


@app.get("/internal/users/by-phone/{phone_hash}", response_model=UserOut)
async def get_user_by_phone(
    phone_hash: str, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    return UserOut(user_id=STATIC_USER_ID, linked=True, frozen=False)


@app.post("/internal/link-tokens", response_model=LinkTokenOut)
async def create_link_token(
    payload: LinkTokenRequest, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    return LinkTokenOut(url="http://localhost:8003/link?t=fake-token-xyz", expires_at=expires_at)


@app.get("/internal/users/{user_id}/wa-target")
async def get_wa_target(
    user_id: uuid.UUID, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    return {"wa_id": "8801700000000"}


@app.post("/internal/users/{user_id}/freeze")
async def freeze_user(
    user_id: uuid.UUID, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    return {"status": "frozen", "user_id": str(user_id)}


@app.post("/internal/approvals", response_model=ApprovalCreated)
async def create_approval(
    payload: ApprovalCreate, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    app_id = uuid.uuid4()
    return ApprovalCreated(
        approval_id=app_id, url=f"http://localhost:8003/approve/{app_id}?t=mock-approval-token"
    )


@app.post("/internal/verify-signature", response_model=VerifySignatureOut)
async def verify_signature(
    payload: VerifySignatureIn, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    return VerifySignatureOut(valid=True)


@app.post("/internal/upload-links")
async def create_upload_link(
    payload: dict, x_internal_key: str | None = Header(None, alias="X-Internal-Key")
):
    check_internal_key(x_internal_key)
    doc_id = uuid.uuid4()
    return {"url": f"http://localhost:8003/upload/{doc_id}?t=mock-upload-token"}
