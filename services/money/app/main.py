import logging
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

from contracts.models import (
    ApprovalConfirmed,
    BalanceOut,
    ProposalResult,
    RateOut,
    RuleProposal,
    TransferProposal,
)
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import background, jobs, proposals
from app.auth import require_internal_key
from app.config import settings
from app.db import SessionLocal, get_session
from app.executor import execute_transfer
from app.fx import PAIRS, RateStale, get_rate
from app.ledger import get_balance, seed_user_funds
from app.models import Mandate
from app.providers.mock import MockProvider
from app.redis_client import redis
from app.rules_engine import freeze_user, index_rule

# One provider for the whole process (the mock keeps its records in memory)
provider = MockProvider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.run_jobs_in_api:
        yield
        return
    logging.basicConfig(level=logging.INFO)  # like python -m app.jobs
    async with background.running({"money-jobs": lambda: jobs.main(provider)}):
        yield


app = FastAPI(title="Money Core", lifespan=lifespan)
internal = [Depends(require_internal_key)]
new_session = SessionLocal


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/internal/rates/{pair}", dependencies=internal)
async def read_rate(pair: str) -> RateOut:
    if pair not in PAIRS:
        raise HTTPException(404, "unknown pair")
    try:
        return await get_rate(redis, pair)
    except RateStale:
        raise HTTPException(503, "rate unavailable") from None


@app.get("/internal/balance/{user_id}", dependencies=internal)
async def read_balance(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return BalanceOut(user_id=user_id, balances=await get_balance(session, user_id))


@app.get("/internal/rules/{user_id}", dependencies=internal)
async def read_rules(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    mandates = await session.scalars(select(Mandate).where(Mandate.user_id == user_id))
    return [
        {
            "id": m.id,
            "pair": f"{m.from_currency}_{m.to_currency}",
            "target_rate": m.target_rate,
            "status": m.state,
        }
        for m in mandates
    ]


@app.post("/internal/proposals/transfer", dependencies=internal)
async def propose_transfer(
    proposal: TransferProposal, session: AsyncSession = Depends(get_session)
) -> ProposalResult:
    return await proposals.propose_transfer(session, redis, proposal)


@app.post("/internal/proposals/rule", dependencies=internal)
async def propose_rule(
    rule: RuleProposal, session: AsyncSession = Depends(get_session)
) -> ProposalResult:
    return await proposals.propose_rule(session, redis, rule)


@app.post("/internal/approvals/confirmed", dependencies=internal)
async def approval_confirmed(
    confirmed: ApprovalConfirmed,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    try:
        kind = await proposals.confirm_approval(session, confirmed)
    except proposals.ConfirmError as e:
        raise HTTPException(e.status_code, e.detail) from None
    if kind == "transfer":
        background.add_task(run_transfer, confirmed.subject_id)
    else:
        await index_rule(redis, await session.get(Mandate, confirmed.subject_id))
    return {"ok": True}


@app.post("/internal/users/{user_id}/freeze-money", dependencies=internal)
async def freeze_money(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await freeze_user(session, redis, user_id)
    return {"frozen": True}


async def run_transfer(transfer_id: uuid.UUID) -> None:
    # Own session: the request's session is closed by the time this runs
    async with new_session() as session:
        await execute_transfer(session, transfer_id, provider, redis)


# Dev-only: the route doesn't even exist outside dev/test
if settings.env in {"dev", "test"}:

    @app.post("/dev/seed/{user_id}", dependencies=internal)
    async def dev_seed(
        user_id: uuid.UUID,
        amount: Decimal = Decimal("10000"),
        currency: str = "BDT",
        session: AsyncSession = Depends(get_session),
    ):
        await seed_user_funds(session, user_id, amount, currency)
        await session.commit()
        return BalanceOut(user_id=user_id, balances=await get_balance(session, user_id))
