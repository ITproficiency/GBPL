import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import poller
from routers import api


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller.run_poller())
    yield
    task.cancel()


app = FastAPI(
    title="gPBL",
    description="Read Firebase sensor data + LLM advice",
    version="2.0.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"

app.include_router(api.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard():
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
