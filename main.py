from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mushroomsai"}
