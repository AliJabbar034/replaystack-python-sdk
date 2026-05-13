from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

EventStatus = Literal["success", "failed", "warning", "pending"]
EventType = Literal["api", "queue", "webhook", "custom", "cron"]
LogLevel = Literal["debug", "info", "warning", "error"]
AuthMode = Literal["bearer", "basic", "api_key", "cookie", "other", "none"]


@dataclass
class StackFrame:
    function_name: Optional[str] = None
    file_name: Optional[str] = None
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    raw: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        # Backend contract uses camelCase keys.
        mapping = {
            "function_name": "functionName",
            "file_name": "fileName",
            "line_number": "lineNumber",
            "column_number": "columnNumber",
        }
        return {mapping.get(k, k): v for k, v in asdict(self).items() if v is not None}


@dataclass
class Breadcrumb:
    message: str
    timestamp: str
    category: Optional[str] = None
    level: LogLevel = "info"
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ReplayStackLog:
    level: LogLevel
    message: str
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class CaptureResponse:
    """Response returned from the ingest API after a successful capture."""

    success: bool
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@dataclass
class ReplayStackConfig:
    """Strongly-typed configuration mirror of the TS SDK's `ReplayStackConfig`."""

    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    ingest_url: Optional[str] = None
    service_name: Optional[str] = None
    environment: Optional[str] = None
    app_version: Optional[str] = None
    commit_hash: Optional[str] = None
    enabled: Optional[bool] = None
    timeout_seconds: float = 2.5
    max_retries: int = 1
    sample_rate: float = 1.0
    capture_success: bool = True
    max_payload_bytes: int = 512 * 1024
    mask_fields: List[str] = field(default_factory=list)
    ignored_paths: List[str] = field(default_factory=list)
    max_breadcrumbs: int = 50
    offline_queue_max: int = 100
    flush_interval_seconds: float = 0.0
    async_send: bool = True
    raise_on_error: bool = False
    on_error: Optional[Callable[[BaseException], None]] = None
    on_queue_drop: Optional[Callable[[Dict[str, Any]], None]] = None


@dataclass
class ReplayStackEvent:
    """Event captured by the SDK and shipped to the backend after camelCase mapping."""

    event_type: EventType
    status: EventStatus
    method: Optional[str] = None
    endpoint: Optional[str] = None
    request_url: Optional[str] = None
    trace_id: Optional[str] = None
    auth_mode: Optional[AuthMode] = None
    auth_scheme: Optional[str] = None
    request_headers: Optional[Dict[str, Any]] = None
    request_payload: Optional[Any] = None
    response_headers: Optional[Dict[str, Any]] = None
    response_payload: Optional[Any] = None
    status_code: Optional[int] = None
    execution_time_ms: Optional[int] = None
    error_name: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    stack_frames: Optional[List[Dict[str, Any]]] = None
    breadcrumbs: Optional[List[Dict[str, Any]]] = None
    service_name: Optional[str] = None
    environment: Optional[str] = None
    app_version: Optional[str] = None
    commit_hash: Optional[str] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    logs: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None

    _SNAKE_TO_CAMEL = {
        "event_type": "eventType",
        "trace_id": "traceId",
        "request_url": "requestUrl",
        "auth_mode": "authMode",
        "auth_scheme": "authScheme",
        "request_headers": "requestHeaders",
        "request_payload": "requestPayload",
        "response_headers": "responseHeaders",
        "response_payload": "responsePayload",
        "status_code": "statusCode",
        "execution_time_ms": "executionTimeMs",
        "error_name": "errorName",
        "error_message": "errorMessage",
        "stack_trace": "stackTrace",
        "stack_frames": "stackFrames",
        "service_name": "serviceName",
        "app_version": "appVersion",
        "commit_hash": "commitHash",
        "source_ip": "sourceIp",
        "user_agent": "userAgent",
    }

    def to_api_payload(self) -> Dict[str, Any]:
        data = asdict(self)
        payload: Dict[str, Any] = {}
        for key, value in data.items():
            if value is None:
                continue
            payload[self._SNAKE_TO_CAMEL.get(key, key)] = value
        return payload
