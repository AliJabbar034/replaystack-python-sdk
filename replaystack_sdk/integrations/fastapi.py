from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

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
TraceIdGetter = Callable[[Request], Optional[str]]


@dataclass
class FastAPIMiddlewareOptions:
    """Mirror of TS `ExpressMiddlewareOptions` for Starlette/FastAPI."""

    capture_request_body: bool = True
    capture_response_body: bool = True
    capture_headers: bool = True
    ignored_paths: List[str] = field(default_factory=list)
    get_trace_id: Optional[TraceIdGetter] = None
    should_capture: Optional[CaptureDecision] = None


class ReplayStackFastAPIMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware mirroring the TS Express middleware surface."""

    def __init__(
        self,
        app: Any,
        client: ReplayStackClient,
        options: Optional[FastAPIMiddlewareOptions] = None,
    ) -> None:
        super().__init__(app)
        self.client = client
        self.options = options or FastAPIMiddlewareOptions()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = normalize_endpoint(request.url.path)
        ignored_paths = list(DEFAULT_IGNORED_PATHS) + list(self.options.ignored_paths or [])
        if should_ignore_path(path, ignored_paths):
            return await call_next(request)

        with run_with_replaystack_context():
            return await self._dispatch_inner(request, call_next, path)

    async def _dispatch_inner(self, request: Request, call_next: Callable, path: Optional[str]) -> Response:
        started_at = time.time()
        trace_id = (
            (self.options.get_trace_id(request) if self.options.get_trace_id else None)
            or request.headers.get("x-trace-id")
            or create_trace_id()
        )
        request_url = build_absolute_request_url_from_parts(
            path_with_query=str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
            get_header=lambda name: request.headers.get(name),
            protocol_fallback=request.url.scheme,
        )

        request_body: Any = None
        if self.options.capture_request_body:
            try:
                body_bytes = await request.body()
                request_body = body_bytes.decode("utf-8") if body_bytes else None
            except Exception:
                request_body = None

        self.client.add_breadcrumb(
            "HTTP request started",
            category="http",
            level="info",
            metadata={"method": request.method, "endpoint": path},
        )

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            execution_time_ms = int((time.time() - started_at) * 1000)
            details = get_error_details(exc)
            self.client.add_breadcrumb(
                "Unhandled exception captured",
                category="exception",
                level="error",
                metadata={"errorName": details["errorName"], "errorMessage": details["errorMessage"]},
            )
            self.client.capture_event(
                event_type="api",
                method=request.method,
                endpoint=path,
                request_url=request_url,
                trace_id=trace_id,
                request_headers=headers_to_object(request.headers) if self.options.capture_headers else None,
                request_payload=request_body,
                response_payload={"message": details["errorMessage"]},
                status="failed",
                status_code=500,
                execution_time_ms=execution_time_ms,
                error_name=details["errorName"],
                error_message=details["errorMessage"],
                stack_trace=details["stackTrace"],
                stack_frames=details["stackFrames"],
                source_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            raise

        execution_time_ms = int((time.time() - started_at) * 1000)
        status_code = response.status_code
        status = "failed" if status_code >= 500 else "warning" if status_code >= 400 else "success"

        decision = True
        if self.options.should_capture is not None:
            try:
                decision = bool(
                    self.options.should_capture(
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

        response.headers.setdefault("x-trace-id", trace_id)

        if not decision:
            return response

        response_payload: Any = None
        if self.options.capture_response_body and not isinstance(response, StreamingResponse):
            response_payload = await _safe_read_body(response)

        self.client.add_breadcrumb(
            "HTTP request finished",
            category="http",
            level="error" if status == "failed" else "warning" if status == "warning" else "info",
            metadata={"statusCode": status_code, "executionTimeMs": execution_time_ms},
        )

        self.client.capture_event(
            event_type="api",
            method=request.method,
            endpoint=path,
            request_url=request_url,
            trace_id=trace_id,
            request_headers=headers_to_object(request.headers) if self.options.capture_headers else None,
            request_payload=request_body,
            response_headers=headers_to_object(response.headers) if self.options.capture_headers else None,
            response_payload=response_payload
            if response_payload is not None
            else {"captured": False, "reason": "Streaming response body not captured"},
            status=status,
            status_code=status_code,
            execution_time_ms=execution_time_ms,
            source_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return response


async def _safe_read_body(response: Response) -> Any:
    """Best-effort capture of a non-streaming response body."""

    body = getattr(response, "body", None)
    if body is None:
        return None
    try:
        text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
        text = text[:5000]
        if text and text[0] in "{[":
            try:
                import json

                return json.loads(text)
            except Exception:
                return text
        return text
    except Exception:
        return None


def setup_fastapi(
    app: Any,
    client: ReplayStackClient,
    options: Optional[FastAPIMiddlewareOptions] = None,
) -> None:
    app.add_middleware(ReplayStackFastAPIMiddleware, client=client, options=options or FastAPIMiddlewareOptions())
