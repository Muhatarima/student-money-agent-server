from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import users

HERE = Path(__file__).parent.parent

app = FastAPI(title="Identity")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(users.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
