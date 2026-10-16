from contracts.models import ApprovalConfirmed
from fastapi import FastAPI, Header, HTTPException, status

app = FastAPI(title="Mock Money Service")

# Storage to inspect confirmed calls during tests
confirmed_approvals: list[dict] = []


@app.post("/internal/approvals/confirmed")
async def confirm_approval(
    payload: ApprovalConfirmed,
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
):
    if not x_internal_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Internal-Key"
        )

    confirmed_approvals.append(payload.model_dump())
    return {"status": "ok", "approval_id": str(payload.approval_id)}
