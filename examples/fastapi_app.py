"""Minimal FastAPI app wired to the ReplayStack SDK.

Run with:
    REPLAYSTACK_API_KEY=rs_live_xxx uvicorn examples.fastapi_app:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from replaystack_sdk import (
    ReplayStackClient,
    install_replaystack_process_guards,
)
from replaystack_sdk.integrations.fastapi import (
    FastAPIMiddlewareOptions,
    setup_fastapi,
)


replaystack = ReplayStackClient(
    service_name="fastapi-example",
    environment="development",
    flush_interval_seconds=10,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    unsubscribe = install_replaystack_process_guards(replaystack)
    try:
        yield
    finally:
        replaystack.close(timeout_seconds=5)
        unsubscribe()


app = FastAPI(lifespan=lifespan)

setup_fastapi(
    app,
    replaystack,
    FastAPIMiddlewareOptions(
        ignored_paths=["/internal/*"],
        get_trace_id=lambda req: req.headers.get("x-request-id"),
    ),
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/orders")
async def orders(payload: dict):
    replaystack.add_breadcrumb("Order endpoint started", category="http")
    return {"success": True, "payload": payload}


@app.get("/boom")
async def boom():
    replaystack.add_breadcrumb("about to raise", level="warning")
    raise RuntimeError("intentional failure for demo")
