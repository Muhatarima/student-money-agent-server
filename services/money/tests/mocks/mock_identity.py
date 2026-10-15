"""Fake identity service (Engineer C) for local testing.
Run from services/money:  uvicorn tests.mocks.mock_identity:app --port 8003
"""

import uuid

from contracts.models import ApprovalCreate, ApprovalCreated, VerifySignatureIn, VerifySignatureOut
from fastapi import Depends, FastAPI

from app.auth import require_internal_key

app = FastAPI(title="Mock Identity", dependencies=[Depends(require_internal_key)])


@app.post("/internal/approvals")
async def create_approval(data: ApprovalCreate) -> ApprovalCreated:
    approval_id = uuid.uuid4()
    return ApprovalCreated(
        approval_id=approval_id, url=f"http://localhost:8003/approve/{approval_id}"
    )


@app.post("/internal/verify-signature")
async def verify_signature(data: VerifySignatureIn) -> VerifySignatureOut:
    return VerifySignatureOut(valid=True)
