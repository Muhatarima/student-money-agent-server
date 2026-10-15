from fastapi import FastAPI

app = FastAPI(title="Money Core")


@app.get("/health")
async def health():
    return {"status": "ok"}
