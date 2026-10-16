"""Fake Money service (Engineer B): accepts /internal/approvals/confirmed.
Run from services/identity:  uvicorn tests.mocks.mock_money:app --port 8002
"""

from contracts.models import ApprovalConfirmed
from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Mock Money")
confirmed: list[ApprovalConfirmed] = []  # what Identity told us
answer = {"status": 200}  # tests can make it refuse


@app.post("/internal/approvals/confirmed")
async def approvals_confirmed(body: ApprovalConfirmed, x_internal_key: str = Header("")):
    if x_internal_key != "test-key":  # tests only; the real money service uses compare_digest
        raise HTTPException(401, "bad key")
    if answer["status"] != 200:
        raise HTTPException(answer["status"], "refused")
    confirmed.append(body)
    print(f"confirmed approval {body.approval_id}")  # no personal data in here
    return {"ok": True}
