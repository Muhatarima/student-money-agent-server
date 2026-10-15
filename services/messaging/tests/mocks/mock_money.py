"""Fake Money service (Engineer B) with made-up data.
Run from services/messaging:  uvicorn tests.mocks.mock_money:app --port 8002
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from contracts.models import BalanceOut, ProposalResult, RateOut, RuleProposal, TransferProposal
from fastapi import Depends, FastAPI, HTTPException

from tests.mocks.auth import require_internal_key

app = FastAPI(title="Mock Money", dependencies=[Depends(require_internal_key)])

RATES = {"BDT_IDR": "135.20", "BDT_USD": "0.0082", "USD_IDR": "16500"}
APPROVAL_URL = "http://localhost:8003/approve/demo"


def pending() -> ProposalResult:
    return ProposalResult(
        status="pending_approval", approval_url=APPROVAL_URL, subject_id=uuid.uuid4()
    )


@app.get("/internal/rates/{pair}")
async def rate(pair: str) -> RateOut:
    if pair not in RATES:
        raise HTTPException(404, "unknown pair")
    return RateOut(pair=pair, rate=Decimal(RATES[pair]), fetched_at=datetime.now(UTC))


@app.get("/internal/balance/{user_id}")
async def balance(user_id: uuid.UUID) -> BalanceOut:
    return BalanceOut(user_id=user_id, balances={"BDT": Decimal("5000.00"), "IDR": Decimal("0")})


@app.post("/internal/proposals/transfer")
async def propose_transfer(proposal: TransferProposal) -> ProposalResult:
    return pending()


@app.post("/internal/proposals/rule")
async def propose_rule(proposal: RuleProposal) -> ProposalResult:
    return pending()


@app.get("/internal/rules/{user_id}")
async def rules(user_id: uuid.UUID) -> list[dict]:
    return [{"id": str(uuid.uuid4()), "pair": "BDT_IDR", "target_rate": "136", "status": "active"}]


@app.post("/internal/users/{user_id}/freeze-money")
async def freeze_money(user_id: uuid.UUID) -> dict:
    return {"frozen": True}
