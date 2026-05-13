from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from flask import Flask, g, request

from replaystack_sdk.client import ReplayStackClient
from replaystack_sdk.context import run_with_replaystack_context
from replaystack_sdk.utils import (
    build_absolute_request_url_from_parts,
    create_trace_id,
    get_error_details,
    headers_to_object,
    normalize_endpoint,
    should_ignore_path,
)

DEFAULT_IGNORED_PATHS = ["/health", "/metrics", "/favicon.ico"]


CaptureDecision = Callable[[dict], bool]
TraceIdGetter = Callable[[Any], Optional[str]]


@dataclass
class FlaskMiddlewareOptions:
    """Mirror of TS `ExpressMiddlewareOptions`."""

    capture_request_body: bool = True
    capture_response_body: bool = True
    capture_headers: bool = True
    ignored_paths: List[str] = field(default_factory=list)
    get_trace_id: Optional[TraceIdGetter] = None
    should_capture: Optional[CaptureDecision] = None


def setup_flask(
    app: Flask,
    client: ReplayStackClient,
    options: Optional[FlaskMiddlewareOptions] = None,
) -> None:
    """Register ReplayStack hooks on a Flask app."""

    opts = options or FlaskMiddlewareOptions()
    ignored_paths = list(DEFAULT_IGNORED_PATHS) + list(opts.ignored_paths or [])

    @app.before_request
    def replaystack_before_request() -> None:
        path = normalize_endpoint(request.path)
        if should_ignore_path(path, ignored_paths):
            g.replaystack_skipped = True
            return

        ctx_manager = run_with_replaystack_context()
        ctx_manager.__enter__()
        g.replaystack_ctx_manager = ctx_manager

        started_at = time.time()
        g.replaystack_started_at = started_at

        trace_id = (opts.get_trace_id(request) if opts.get_trace_id else None) or request.headers.get("x-trace-id") or create_trace_id()
        g.replaystack_trace_id = trace_id

        if opts.capture_request_body:
            try:
                g.replaystack_request_payload = request.get_json(silent=True) or request.get_data(as_text=True) or None
            except Exception:
                g.replaystack_request_payload = None
        else:
            g.replaystack_request_payload = None

        client.add_breadcrumb(
            "HTTP request started",
            category="http",
            level="info",
            metadata={"method": request.method, "endpoint": path or request.path},
        )

    @app.after_request
    def replaystack_after_request(response):
        if getattr(g, "replaystack_skipped", False):
            return response

        started_at = getattr(g, "replaystack_started_at", time.time())
        execution_time_ms = int((time.time() - started_at) * 1000)
        status_code = response.status_code
        status = "failed" if status_code >= 500 else "warning" if status_code >= 400 else "success"
        path = normalize_endpoint(request.path) or request.path
        trace_id = getattr(g, "replaystack_trace_id", None) or create_trace_id()
        response.headers.setdefault("x-trace-id", trace_id)

        decision = True
        if opts.should_capture is not None:
            try:
                decision = bool(
                    opts.should_capture(
                        {
                            "method": request.method,
                            "path": path,
                            "statusCode": status_code,
                            "executionTimeMs": execution_time_ms,
                        }
                    )
                )
            except Exception:
                decision = True

        if not decision:
            return response

        response_payload: Any = None
        if opts.capture_response_body:
            try:
                if response.is_json:
                    response_payload = response.get_json(silent=True)
                else:
                    response_payload = response.get_data(as_text=True)[:5000]
            except Exception:
                response_payload = None

        request_url = build_absolute_request_url_from_parts(
            path_with_query=request.full_path if request.query_string else request.path,
            get_header=lambda name: request.headers.get(name),
            protocol_fallback=request.scheme,
        )

        client.add_breadcrumb(
            "HTTP request finished",
            category="http",
            level="error" if status == "failed" else "warning" if status == "warning" else "info",
            metadata={"statusCode": status_code, "executionTimeMs": execution_time_ms},
        )

        client.capture_event(
            event_type="api",
            method=request.method,
            endpoint=path,
            request_url=request_url,
            trace_id=trace_id,
            request_headers=headers_to_object(request.headers) if opts.capture_headers else None,
            request_payload=getattr(g, "replaystack_request_payload", None),
            response_headers=headers_to_object(response.headers) if opts.capture_headers else None,
            response_payload=response_payload,
            status=status,
            status_code=status_code,
            execution_time_ms=execution_time_ms,
            source_ip=request.remote_addr,
            user_agent=request.headers.get("user-agent"),
        )
        return response

    @app.teardown_request
    def replaystack_teardown(_exc=None):
        ctx_manager = getattr(g, "replaystack_ctx_manager", None)
        if ctx_manager is not None:
            try:
                ctx_manager.__exit__(None, None, None)
            except Exception:
                pass

    @app.errorhandler(Exception)
    def replaystack_error_handler(exc: Exception):
        if getattr(g, "replaystack_skipped", False):
            raise exc

        started_at = getattr(g, "replaystack_started_at", time.time())
        execution_time_ms = int((time.time() - started_at) * 1000)
        details = get_error_details(exc)
        trace_id = getattr(g, "replaystack_trace_id", None) or create_trace_id()
        path = normalize_endpoint(request.path) or request.path

        request_url = build_absolute_request_url_from_parts(
            path_with_query=request.full_path if request.query_string else request.path,
            get_header=lambda name: request.headers.get(name),
            protocol_fallback=request.scheme,
        )

        client.add_breadcrumb(
            "Unhandled exception captured",
            category="exception",
            level="error",
            metadata={"errorName": details["errorName"], "errorMessage": details["errorMessage"]},
        )

        client.capture_event(
            event_type="api",
            method=request.method,
            endpoint=path,
            request_url=request_url,
            trace_id=trace_id,
            request_headers=headers_to_object(request.headers) if opts.capture_headers else None,
            request_payload=getattr(g, "replaystack_request_payload", None) if opts.capture_request_body else None,
            response_payload={"message": details["errorMessage"]},
            status="failed",
            status_code=500,
            execution_time_ms=execution_time_ms,
            error_name=details["errorName"],
            error_message=details["errorMessage"],
            stack_trace=details["stackTrace"],
            stack_frames=details["stackFrames"],
            source_ip=request.remote_addr,
            user_agent=request.headers.get("user-agent"),
        )
        raise exc
