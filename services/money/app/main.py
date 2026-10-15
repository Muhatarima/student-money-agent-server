from contracts.models import RateOut
from fastapi import Depends, FastAPI, HTTPException

from app.auth import require_internal_key
from app.fx import PAIRS, RateStale, get_rate
from app.redis_client import redis

app = FastAPI(title="Money Core")
internal = [Depends(require_internal_key)]


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
