from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).parent.parent

app = FastAPI(title="Identity")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}
