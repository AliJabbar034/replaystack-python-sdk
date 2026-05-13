from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

import requests

from .context import (
    add_breadcrumb as context_add_breadcrumb,
)
from .context import (
    clear_breadcrumbs,
    clear_context_breadcrumbs,
    get_breadcrumbs,
    get_context_breadcrumbs,
)
from .types import CaptureResponse, ReplayStackEvent
from .utils import (
    create_trace_id,
    detect_auth_mode,
    get_error_details,
    mask_sensitive_data,
    normalize_endpoint,
    safe_json_clone,
    should_ignore_path,
    should_sample,
    truncate_payload,
    truncate_utf8_string,
)

logger = logging.getLogger("replaystack")


def _resolve_sdk_version() -> str:
    """Pull the installed package version, falling back to a sentinel when unbuilt."""

    try:
        from importlib.metadata import version

        return version("replaystack-sdk")
    except Exception:  # noqa: BLE001
        return "0.0.0+local"


SDK_NAME = "python"
SDK_VERSION = _resolve_sdk_version()
DEFAULT_ENDPOINT = "https://api.replaystack.co"
DEFAULT_INGEST_PATH = "/api/v1/ingest/events"
DEFAULT_TIMEOUT_SECONDS = 2.5
DEFAULT_RETRIES = 1
DEFAULT_MAX_PAYLOAD_SIZE = 512 * 1024
DEFAULT_MAX_BREADCRUMBS = 50
DEFAULT_OFFLINE_QUEUE_MAX = 100


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class ReplayStackClient:
    """Python SDK client matching the TS `ReplayStack` surface."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        ingest_url: Optional[str] = None,
        service_name: Optional[str] = None,
        environment: Optional[str] = None,
        app_version: Optional[str] = None,
        commit_hash: Optional[str] = None,
        enabled: Optional[bool] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        sample_rate: Optional[float] = None,
        capture_success: Optional[bool] = None,
        max_payload_bytes: Optional[int] = None,
        mask_fields: Optional[Iterable[str]] = None,
        ignored_paths: Optional[Iterable[str]] = None,
        max_breadcrumbs: Optional[int] = None,
        offline_queue_max: Optional[int] = None,
        flush_interval_seconds: Optional[float] = None,
        async_send: bool = True,
        raise_on_error: bool = False,
        on_error: Optional[Callable[[BaseException], None]] = None,
        on_queue_drop: Optional[Callable[[Dict[str, Any]], None]] = None,
        # `requests.Session`-like adapter for tests; must implement `.post(...)`.
        transport: Any = None,
    ) -> None:
        self.api_key = api_key or os.getenv("REPLAYSTACK_API_KEY")
        if not self.api_key:
            # Match TS: hard fail when no key is supplied; users can pass `enabled=False`
            # explicitly to silence the SDK without removing code.
            raise ValueError("ReplayStack apiKey is required.")

        base_endpoint = endpoint or os.getenv("REPLAYSTACK_ENDPOINT") or DEFAULT_ENDPOINT
        self.endpoint = base_endpoint.rstrip("/")
        self.ingest_url = (
            ingest_url
            or os.getenv("REPLAYSTACK_INGEST_URL")
            or f"{self.endpoint}{DEFAULT_INGEST_PATH}"
        )

        self.service_name = service_name or os.getenv("REPLAYSTACK_SERVICE_NAME")
        self.environment = (
            environment
            or os.getenv("REPLAYSTACK_ENVIRONMENT")
            or os.getenv("ENVIRONMENT")
            or os.getenv("PYTHON_ENV")
            or os.getenv("APP_ENV")
            or "development"
        )
        self.app_version = app_version or os.getenv("REPLAYSTACK_APP_VERSION") or os.getenv("APP_VERSION")
        self.commit_hash = commit_hash or os.getenv("REPLAYSTACK_COMMIT_HASH") or os.getenv("COMMIT_HASH")

        self.enabled = enabled if enabled is not None else _env_bool("REPLAYSTACK_ENABLED", True)
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds
        elif os.getenv("REPLAYSTACK_TIMEOUT_MS"):
            self.timeout_seconds = _env_float("REPLAYSTACK_TIMEOUT_MS", DEFAULT_TIMEOUT_SECONDS * 1000) / 1000
        else:
            self.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else _env_int("REPLAYSTACK_RETRIES", DEFAULT_RETRIES)
        self.sample_rate = sample_rate if sample_rate is not None else _env_float("REPLAYSTACK_SAMPLE_RATE", 1.0)
        self.capture_success = (
            capture_success if capture_success is not None else _env_bool("REPLAYSTACK_CAPTURE_SUCCESS", True)
        )
        self.max_payload_bytes = (
            max_payload_bytes
            if max_payload_bytes is not None
            else _env_int("REPLAYSTACK_MAX_PAYLOAD_SIZE_BYTES", DEFAULT_MAX_PAYLOAD_SIZE)
        )
        self.mask_fields: List[str] = list(mask_fields or [])
        self.ignored_paths: List[str] = list(ignored_paths or [])
        self.max_breadcrumbs = (
            max_breadcrumbs if max_breadcrumbs is not None else _env_int("REPLAYSTACK_MAX_BREADCRUMBS", DEFAULT_MAX_BREADCRUMBS)
        )
        self.offline_queue_max = (
            offline_queue_max
            if offline_queue_max is not None
            else _env_int("REPLAYSTACK_OFFLINE_QUEUE_MAX", DEFAULT_OFFLINE_QUEUE_MAX)
        )
        self.flush_interval_seconds = (
            flush_interval_seconds
            if flush_interval_seconds is not None
            else _env_float("REPLAYSTACK_FLUSH_INTERVAL_MS", 0.0) / 1000
        )

        self.async_send = async_send
        self.raise_on_error = raise_on_error
        self.on_error = on_error
        self.on_queue_drop = on_queue_drop

        self._transport = transport or requests
        self._closed = False
        self._drain_mode = False
        self._offline_queue: Deque[Dict[str, Any]] = deque()
        self._queue_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._breadcrumbs: List[Dict[str, Any]] = []

        self._flush_timer: Optional[threading.Thread] = None
        self._flush_stop_event: Optional[threading.Event] = None
        if self.flush_interval_seconds > 0:
            self._start_flush_timer()

    # ------------------------------------------------------------------ API

    def add_breadcrumb(
        self,
        message: str,
        *,
        category: Optional[str] = None,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not message:
            return
        breadcrumb = context_add_breadcrumb(
            message,
            category=category,
            level=level,
            metadata=metadata,
            max_breadcrumbs=self.max_breadcrumbs,
        )
        # When no context scope is active the helper falls back to a global ContextVar.
        # We still keep a per-client mirror so `getBreadcrumbs` returns predictable data
        # to callers that never opened a request scope (e.g. cron jobs).
        if get_context_breadcrumbs() is None:
            self._breadcrumbs.append(breadcrumb.to_dict())
            if self.max_breadcrumbs > 0 and len(self._breadcrumbs) > self.max_breadcrumbs:
                self._breadcrumbs = self._breadcrumbs[-self.max_breadcrumbs :]

    def clear_breadcrumbs(self) -> None:
        if not clear_context_breadcrumbs():
            clear_breadcrumbs()
        self._breadcrumbs = []

    def get_breadcrumbs(self) -> List[Dict[str, Any]]:
        ctx = get_context_breadcrumbs()
        if ctx is not None:
            return list(ctx)
        # Prefer the per-client mirror (already deduplicated against the context).
        if self._breadcrumbs:
            return list(self._breadcrumbs)
        return get_breadcrumbs()

    def should_ignore_path(self, path: Optional[str]) -> bool:
        return should_ignore_path(path, self.ignored_paths)

    def capture_event(self, **kwargs: Any) -> Optional[CaptureResponse]:
        try:
            if self._closed or not self.enabled:
                return None

            raw_endpoint = kwargs.get("endpoint")
            endpoint = normalize_endpoint(raw_endpoint) if raw_endpoint is not None else raw_endpoint
            if endpoint is not None:
                kwargs["endpoint"] = endpoint
            status = kwargs.get("status", "success")

            if self.should_ignore_path(endpoint):
                return None
            if not should_sample(self.sample_rate):
                return None
            if status == "success" and not self.capture_success:
                return None

            event = self._build_event(kwargs)
            payload = self._prepare_payload(event)

            if self.async_send:
                thread = threading.Thread(
                    target=self._send_with_retries,
                    args=(payload,),
                    daemon=True,
                )
                thread.start()
                return None
            return self._send_with_retries(payload)
        except BaseException as exc:  # noqa: BLE001
            self._report_internal_error(exc)
            if self.raise_on_error:
                raise
            return None

    def capture_exception(self, exc: BaseException, **kwargs: Any) -> Optional[CaptureResponse]:
        details = get_error_details(exc)
        kwargs.setdefault("event_type", "custom")
        kwargs["status"] = "failed"
        kwargs.setdefault("status_code", 500)
        kwargs.setdefault("error_name", details["errorName"])
        kwargs.setdefault("error_message", details["errorMessage"])
        kwargs.setdefault("stack_trace", details["stackTrace"])
        kwargs.setdefault("stack_frames", details["stackFrames"])
        return self.capture_event(**kwargs)

    def flush(self, timeout_seconds: Optional[float] = None) -> None:
        """Drain the offline queue synchronously. Safe to call repeatedly."""

        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        with self._flush_lock:
            self._drain_offline_queue(deadline)

    def close(self, timeout_seconds: Optional[float] = None) -> None:
        """Stop periodic flush, drain the offline queue, then mark the client closed."""

        if self._closed:
            self.flush(timeout_seconds)
            return
        self._closed = True
        self._stop_flush_timer()
        self.flush(timeout_seconds)

    # -------------------------------------------------------------- internal

    def _build_event(self, kwargs: Dict[str, Any]) -> ReplayStackEvent:
        return ReplayStackEvent(
            event_type=kwargs.get("event_type") or kwargs.get("eventType") or "custom",
            status=kwargs.get("status", "success"),
            method=kwargs.get("method"),
            endpoint=kwargs.get("endpoint"),
            request_url=kwargs.get("request_url") or kwargs.get("requestUrl"),
            trace_id=kwargs.get("trace_id") or kwargs.get("traceId"),
            auth_mode=kwargs.get("auth_mode") or kwargs.get("authMode"),
            auth_scheme=kwargs.get("auth_scheme") or kwargs.get("authScheme"),
            request_headers=kwargs.get("request_headers") or kwargs.get("requestHeaders"),
            request_payload=kwargs.get("request_payload") if "request_payload" in kwargs else kwargs.get("requestPayload"),
            response_headers=kwargs.get("response_headers") or kwargs.get("responseHeaders"),
            response_payload=kwargs.get("response_payload") if "response_payload" in kwargs else kwargs.get("responsePayload"),
            status_code=kwargs.get("status_code") or kwargs.get("statusCode"),
            execution_time_ms=kwargs.get("execution_time_ms") or kwargs.get("executionTimeMs"),
            error_name=kwargs.get("error_name") or kwargs.get("errorName"),
            error_message=kwargs.get("error_message") or kwargs.get("errorMessage"),
            stack_trace=kwargs.get("stack_trace") or kwargs.get("stackTrace"),
            stack_frames=kwargs.get("stack_frames") or kwargs.get("stackFrames"),
            breadcrumbs=kwargs.get("breadcrumbs") or self.get_breadcrumbs() or None,
            service_name=kwargs.get("service_name") or kwargs.get("serviceName") or self.service_name,
            environment=kwargs.get("environment") or self.environment,
            app_version=kwargs.get("app_version") or kwargs.get("appVersion") or self.app_version,
            commit_hash=kwargs.get("commit_hash") or kwargs.get("commitHash") or self.commit_hash,
            source_ip=kwargs.get("source_ip") or kwargs.get("sourceIp"),
            user_agent=kwargs.get("user_agent") or kwargs.get("userAgent"),
            logs=kwargs.get("logs"),
            metadata=kwargs.get("metadata"),
        )

    def _prepare_payload(self, event: ReplayStackEvent) -> Dict[str, Any]:
        payload = event.to_api_payload()
        cloned = safe_json_clone(payload)
        if not isinstance(cloned, dict):
            cloned = payload

        cloned.setdefault("traceId", create_trace_id())
        normalized_endpoint = normalize_endpoint(cloned.get("endpoint"))
        if normalized_endpoint is not None:
            cloned["endpoint"] = normalized_endpoint

        if not cloned.get("authMode"):
            detected = detect_auth_mode(cloned.get("requestHeaders"))
            cloned["authMode"] = detected.get("mode")
            scheme = detected.get("scheme")
            if scheme and not cloned.get("authScheme"):
                cloned["authScheme"] = scheme

        masked = mask_sensitive_data(cloned, self.mask_fields)
        if not isinstance(masked, dict):
            masked = cloned

        max_url_bytes = min(8192, self.max_payload_bytes)
        if isinstance(masked.get("requestUrl"), str):
            masked["requestUrl"] = truncate_utf8_string(masked["requestUrl"], max_url_bytes)

        for field_name in ("requestPayload", "responsePayload", "requestHeaders", "responseHeaders"):
            if field_name in masked and masked[field_name] is not None:
                masked[field_name] = truncate_payload(masked[field_name], self.max_payload_bytes)

        return masked

    def _send_with_retries(self, payload: Dict[str, Any]) -> Optional[CaptureResponse]:
        if not self.enabled:
            return None

        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._send(payload)
                return response
            except BaseException as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.15 * (attempt + 1))

        if last_error is not None:
            self._report_internal_error(last_error)
            if not self._drain_mode:
                self._enqueue_offline(payload)
            if self.raise_on_error:
                raise last_error
        return None

    def _send(self, payload: Dict[str, Any]) -> CaptureResponse:
        headers = {
            "Content-Type": "application/json",
            # Canonical header for TraceReplay backend; legacy alias kept for compat.
            "x-tracereplay-api-key": self.api_key or "",
            "x-replaystack-api-key": self.api_key or "",
            "x-replaystack-sdk": SDK_NAME,
            "x-replaystack-sdk-version": SDK_VERSION,
        }
        response = self._transport.post(
            self.ingest_url,
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        try:
            data = response.json() if response.content else {"success": 200 <= response.status_code < 300}
        except ValueError:
            data = {"success": 200 <= response.status_code < 300}

        if not (200 <= response.status_code < 300):
            message = (data or {}).get("message") if isinstance(data, dict) else None
            raise RuntimeError(
                message or f"ReplayStack ingestion failed with status {response.status_code}"
            )

        if not isinstance(data, dict):
            data = {"success": True}
        return CaptureResponse(
            success=bool(data.get("success", True)),
            message=data.get("message"),
            data=data.get("data") if isinstance(data.get("data"), dict) else None,
        )

    def _enqueue_offline(self, payload: Dict[str, Any]) -> None:
        if self._closed or self._drain_mode:
            return
        if self.offline_queue_max <= 0:
            return
        with self._queue_lock:
            self._offline_queue.append(payload)
            while len(self._offline_queue) > self.offline_queue_max:
                self._offline_queue.popleft()
                try:
                    if self.on_queue_drop:
                        self.on_queue_drop({"reason": "max_queue_size"})
                except Exception:
                    logger.debug("on_queue_drop callback raised", exc_info=True)

    def _drain_offline_queue(self, deadline: Optional[float]) -> None:
        if not self._offline_queue:
            return
        self._drain_mode = True
        try:
            while True:
                with self._queue_lock:
                    if not self._offline_queue:
                        return
                    payload = self._offline_queue[0]
                response = self._send_with_retries(payload)
                if response is None:
                    return
                with self._queue_lock:
                    if self._offline_queue and self._offline_queue[0] is payload:
                        self._offline_queue.popleft()
                if deadline is not None and time.monotonic() > deadline:
                    return
        finally:
            self._drain_mode = False

    def _start_flush_timer(self) -> None:
        if self._flush_timer is not None:
            return
        stop_event = threading.Event()
        self._flush_stop_event = stop_event

        def run() -> None:
            while not stop_event.wait(self.flush_interval_seconds):
                try:
                    self.flush()
                except Exception:
                    logger.debug("periodic flush failed", exc_info=True)

        thread = threading.Thread(target=run, name="replaystack-flush-timer", daemon=True)
        thread.start()
        self._flush_timer = thread

    def _stop_flush_timer(self) -> None:
        if self._flush_stop_event is not None:
            self._flush_stop_event.set()
        self._flush_timer = None
        self._flush_stop_event = None

    def _report_internal_error(self, error: BaseException) -> None:
        try:
            if self.on_error is not None:
                self.on_error(error)
                return
        except Exception:
            logger.debug("on_error callback raised", exc_info=True)
        logger.debug("ReplayStack internal error: %s", error)


# Friendly alias mirroring TS `ReplayStack`.
ReplayStack = ReplayStackClient
