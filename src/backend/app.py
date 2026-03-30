"""FastAPI application — A2X Registry backend."""

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Ensure project root is on sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.backend.routers import search, dataset, build, provider
from src.backend.startup import warmup_state, run_warmup

app = FastAPI(
    title="A2X Registry Demo",
    description="Interactive comparison of A2X hierarchical search vs vector retrieval",
    version="1.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(dataset.router)
app.include_router(build.router)
app.include_router(provider.router)


@app.get("/api/warmup-status")
async def warmup_status():
    """Returns current warmup state for the frontend loading screen."""
    return warmup_state


@app.on_event("startup")
async def _startup():
    # Suppress warmup-status polling from uvicorn access log
    class _SuppressWarmupPoll(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/api/warmup-status" not in record.getMessage()
    logging.getLogger("uvicorn.access").addFilter(_SuppressWarmupPoll())

    loop = asyncio.get_event_loop()
    loop.run_in_executor(ThreadPoolExecutor(1), run_warmup)


# Serve built frontend in production — must be LAST (catches all unmatched paths)
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
