from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

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
class DjangoMiddlewareOptions:
    """Mirror of TS `ExpressMiddlewareOptions` for Django."""

    capture_request_body: bool = True
    capture_response_body: bool = True
    capture_headers: bool = True
    ignored_paths: List[str] = field(default_factory=list)
    get_trace_id: Optional[TraceIdGetter] = None
    should_capture: Optional[CaptureDecision] = None


def _get_client_and_options(request) -> tuple[ReplayStackClient, DjangoMiddlewareOptions]:
    try:
        from django.conf import settings

        client = getattr(settings, "REPLAYSTACK_CLIENT", None)
        options = getattr(settings, "REPLAYSTACK_OPTIONS", None)
    except Exception:
        client = None
        options = None
    if client is None:
        client = ReplayStackClient()
    if options is None:
        options = DjangoMiddlewareOptions()
    return client, options


class ReplayStackDjangoMiddleware:
    """Django middleware mirroring the TS Express middleware surface.

    Configure via:
        settings.REPLAYSTACK_CLIENT = ReplayStackClient(...)
        settings.REPLAYSTACK_OPTIONS = DjangoMiddlewareOptions(...)  # optional
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request):
        client, options = _get_client_and_options(request)
        path = normalize_endpoint(request.path)
        ignored_paths = list(DEFAULT_IGNORED_PATHS) + list(options.ignored_paths or [])
        if should_ignore_path(path, ignored_paths):
            return self.get_response(request)

        with run_with_replaystack_context():
            return self._dispatch(request, client, options, path)

    def _dispatch(self, request, client: ReplayStackClient, options: DjangoMiddlewareOptions, path: Optional[str]):
        started_at = time.time()
        request._replaystack_started_at = started_at
        request._replaystack_client = client
        request._replaystack_options = options

        trace_id = (
            (options.get_trace_id(request) if options.get_trace_id else None)
            or request.headers.get("x-trace-id")
            or create_trace_id()
        )
        request._replaystack_trace_id = trace_id

        if options.capture_request_body:
            try:
                request._replaystack_payload = request.body.decode("utf-8") if request.body else None
            except Exception:
                request._replaystack_payload = None
        else:
            request._replaystack_payload = None

        client.add_breadcrumb(
            "HTTP request started",
            category="http",
            level="info",
            metadata={"method": request.method, "endpoint": path or request.path},
        )

        response = self.get_response(request)
        execution_time_ms = int((time.time() - started_at) * 1000)
        status_code = response.status_code
        status = "failed" if status_code >= 500 else "warning" if status_code >= 400 else "success"

        try:
            response["x-trace-id"] = trace_id
        except Exception:
            pass

        decision = True
        if options.should_capture is not None:
            try:
                decision = bool(
                    options.should_capture(
                        {
                            "method": request.method,
                            "path": path or request.path,
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
        if options.capture_response_body:
            try:
                response_payload = response.content.decode("utf-8")[:5000]
            except Exception:
                response_payload = None

        request_url = build_absolute_request_url_from_parts(
            path_with_query=request.get_full_path() if hasattr(request, "get_full_path") else request.path,
            get_header=lambda name: request.headers.get(name),
            protocol_fallback="https" if request.is_secure() else "http",
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
            endpoint=path or request.path,
            request_url=request_url,
            trace_id=trace_id,
            request_headers=headers_to_object(request.headers) if options.capture_headers else None,
            request_payload=getattr(request, "_replaystack_payload", None),
            response_headers=headers_to_object(response.headers) if options.capture_headers else None,
            response_payload=response_payload,
            status=status,
            status_code=status_code,
            execution_time_ms=execution_time_ms,
            source_ip=request.META.get("REMOTE_ADDR"),
            user_agent=request.headers.get("user-agent"),
        )
        return response

    def process_exception(self, request, exception):
        client = getattr(request, "_replaystack_client", None)
        options = getattr(request, "_replaystack_options", None)
        if client is None or options is None:
            client, options = _get_client_and_options(request)

        started_at = getattr(request, "_replaystack_started_at", time.time())
        execution_time_ms = int((time.time() - started_at) * 1000)
        details = get_error_details(exception)
        trace_id = getattr(request, "_replaystack_trace_id", None) or create_trace_id()
        path = normalize_endpoint(request.path) or request.path

        request_url = build_absolute_request_url_from_parts(
            path_with_query=request.get_full_path() if hasattr(request, "get_full_path") else request.path,
            get_header=lambda name: request.headers.get(name),
            protocol_fallback="https" if request.is_secure() else "http",
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
            request_headers=headers_to_object(request.headers) if options.capture_headers else None,
            request_payload=getattr(request, "_replaystack_payload", None) if options.capture_request_body else None,
            response_payload={"message": details["errorMessage"]},
            status="failed",
            status_code=500,
            execution_time_ms=execution_time_ms,
            error_name=details["errorName"],
            error_message=details["errorMessage"],
            stack_trace=details["stackTrace"],
            stack_frames=details["stackFrames"],
            source_ip=request.META.get("REMOTE_ADDR"),
            user_agent=request.headers.get("user-agent"),
        )
        return None
