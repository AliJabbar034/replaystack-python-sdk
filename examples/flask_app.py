"""Minimal Flask app wired to the ReplayStack SDK.

Run with:
    REPLAYSTACK_API_KEY=rs_live_xxx flask --app examples.flask_app run
"""

from flask import Flask, jsonify, request

from replaystack_sdk import (
    ReplayStackClient,
    install_replaystack_process_guards,
)
from replaystack_sdk.integrations.flask import FlaskMiddlewareOptions, setup_flask


app = Flask(__name__)

replaystack = ReplayStackClient(
    service_name="flask-example",
    environment="development",
    flush_interval_seconds=10,
    on_error=lambda exc: app.logger.warning("replaystack error: %s", exc),
)

setup_flask(
    app,
    replaystack,
    FlaskMiddlewareOptions(
        ignored_paths=["/internal/*"],
        get_trace_id=lambda req: req.headers.get("x-request-id"),
    ),
)

# Capture unhandled exceptions and flush the offline queue on shutdown.
install_replaystack_process_guards(replaystack)


@app.post("/orders")
def orders():
    replaystack.add_breadcrumb("Order endpoint started", category="http")
    return jsonify({"success": True, "payload": request.json})


@app.get("/boom")
def boom():
    replaystack.add_breadcrumb("about to raise", level="warning")
    raise RuntimeError("intentional failure for demo")
