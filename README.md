# ReplayStack Python SDK

ReplayStack's Python SDK captures backend API events, exceptions, stack traces, breadcrumbs, and custom events, then ships them to the ReplayStack ingestion API. It's a feature-aligned port of the official [`@replaystack/sdk`](https://www.npmjs.com/package/@replaystack/sdk) for Node.js.

It supports:

- Manual and middleware-driven event capture
- Manual exception capture with parsed stack frames
- Breadcrumb buffer with request-scoped context
- Sensitive data masking (matches the TS default mask list)
- Configurable timeouts, retries, sampling, and payload truncation
- Offline queue + manual `flush()` / `close()` lifecycle
- Periodic background flush
- Auto-detection of the request's `Authorization` mode (`bearer` / `basic` / `api_key` / `cookie` / `other` / `none`) before masking
- Process guards for unhandled exceptions + graceful shutdown flushing
- FastAPI / Starlette, Flask, and Django integrations with middleware options matching TS Express options
- Queue jobs, cron jobs, background workers, and custom events

Default ingestion URL:

```text
https://api.replaystack.co/api/v1/ingest/events
```

SDK headers sent on every request:

```http
x-tracereplay-api-key: rs_live_xxxxxxxxx
x-replaystack-api-key: rs_live_xxxxxxxxx
x-replaystack-sdk: python
x-replaystack-sdk-version: 1.0.0
```

`x-tracereplay-api-key` is the canonical header for the TraceReplay backend; `x-replaystack-api-key` is kept as a duplicate alias so older receivers keep working.

---

## Installation

```bash
pip install replaystack-sdk
```

For framework extras:

```bash
pip install replaystack-sdk[fastapi]
pip install replaystack-sdk[flask]
pip install replaystack-sdk[django]
pip install replaystack-sdk[all]
```

---

## Environment Variables

The SDK reads the same variables the TS SDK does:

```env
REPLAYSTACK_API_KEY=rs_live_xxxxxxxxxxxxxxxxx
REPLAYSTACK_ENDPOINT=https://api.replaystack.co
REPLAYSTACK_INGEST_URL=https://api.replaystack.co/api/v1/ingest/events
REPLAYSTACK_SERVICE_NAME=order-service
REPLAYSTACK_ENVIRONMENT=production
REPLAYSTACK_APP_VERSION=1.0.0
REPLAYSTACK_COMMIT_HASH=a7f91c

# Runtime tuning
REPLAYSTACK_ENABLED=true
REPLAYSTACK_TIMEOUT_MS=2500
REPLAYSTACK_RETRIES=1
REPLAYSTACK_SAMPLE_RATE=1.0
REPLAYSTACK_CAPTURE_SUCCESS=true
REPLAYSTACK_MAX_PAYLOAD_SIZE_BYTES=524288
REPLAYSTACK_MAX_BREADCRUMBS=50
REPLAYSTACK_OFFLINE_QUEUE_MAX=100
REPLAYSTACK_FLUSH_INTERVAL_MS=0
```

`REPLAYSTACK_COMMIT_HASH` identifies the exact deployed code version that generated the event or exception.

---

## Configuration Reference

Every option can be passed as a keyword argument to `ReplayStackClient(...)`. Environment variables (above) act as defaults when the kwarg is omitted.

| Argument | Type | Default | Notes |
|---|---|---|---|
| `api_key` | `str` | `REPLAYSTACK_API_KEY` env | **Required.** Raises `ValueError` when missing. |
| `endpoint` | `str` | `https://api.replaystack.co` | Base URL (no path). |
| `ingest_url` | `str` | `<endpoint>/api/v1/ingest/events` | Override the full ingest URL. |
| `service_name` | `str` | env | Service shown in dashboard. |
| `environment` | `str` | env / `development` | `local` / `development` / `staging` / `production` / arbitrary string. |
| `app_version` | `str` | env | Release version (e.g. `1.2.0`). |
| `commit_hash` | `str` | env | Git SHA. |
| `enabled` | `bool` | `True` | Disable the SDK at runtime without removing code. |
| `timeout_seconds` | `float` | `2.5` | Request timeout per attempt. |
| `max_retries` | `int` | `1` | Retries on network failure (in addition to the first attempt). |
| `sample_rate` | `float` | `1.0` | `0.1` = capture 10% of events. |
| `capture_success` | `bool` | `True` | When `False`, only `failed`/`warning` events are sent. |
| `max_payload_bytes` | `int` | `524288` (512 KB) | Truncates request/response payloads above this size. |
| `mask_fields` | `Iterable[str]` | `[]` | Custom field names to mask (case-insensitive). |
| `ignored_paths` | `Iterable[str]` | `[]` | Path prefixes or `prefix*` globs to skip. |
| `max_breadcrumbs` | `int` | `50` | Per-request breadcrumb cap. |
| `offline_queue_max` | `int` | `100` | Events buffered after the API errors out. Set to `0` to disable. |
| `flush_interval_seconds` | `float` | `0` | When > 0, a daemon thread calls `flush()` on this interval. |
| `async_send` | `bool` | `True` | When `True`, sends in a background thread. Set `False` for synchronous workers. |
| `raise_on_error` | `bool` | `False` | Re-raise the last transport error after retries (useful in tests). |
| `on_error` | `Callable[[BaseException], None]` | `None` | Internal-error hook (e.g. send to logs/metrics). |
| `on_queue_drop` | `Callable[[dict], None]` | `None` | Fires when offline queue drops the oldest event. |
| `transport` | object | `requests` | Anything exposing `.post(url, json, headers, timeout)`. Useful for tests. |

---

## Basic Usage

```python
from replaystack_sdk import ReplayStackClient

replaystack = ReplayStackClient(
    api_key="rs_live_xxxxxxxxx",
    service_name="order-service",
    environment="production",
    app_version="1.0.0",
    commit_hash="a7f91c",
)

replaystack.capture_event(
    event_type="custom",
    endpoint="daily-report-job",
    status="success",
    response_payload={"message": "Report generated"},
)
```

---

## Manual Exception Capture

```python
try:
    user = None
    user_id = user["id"]
except Exception as exc:
    replaystack.capture_exception(
        exc,
        event_type="custom",
        endpoint="process-user",
        request_payload={"user": user},
    )
    raise
```

The SDK captures:

- error name + error message
- formatted stack trace
- parsed stack frames (`functionName`, `fileName`, `lineNumber`)
- active breadcrumbs

---

## Breadcrumbs

Breadcrumbs are step-by-step debugging hints attached to the next captured event.

```python
replaystack.add_breadcrumb("Order API started")
replaystack.add_breadcrumb("Validating request payload")
replaystack.add_breadcrumb("Calling payment provider", category="payment")
```

Inside a framework integration each request gets its own breadcrumb scope automatically (via `run_with_replaystack_context`). Outside a request, breadcrumbs go into the per-client buffer instead — perfect for cron jobs and background workers.

For custom workers, open a scope manually:

```python
from replaystack_sdk import run_with_replaystack_context

with run_with_replaystack_context():
    replaystack.add_breadcrumb("worker started")
    do_work()
```

---

## Sampling, `captureSuccess`, and Ignored Paths

```python
replaystack = ReplayStackClient(
    api_key="rs_live_xxx",
    sample_rate=0.1,           # capture 10% of events probabilistically
    capture_success=False,     # only ship failed/warning events
    ignored_paths=["/health", "/metrics", "/internal/*"],
)
```

Sampling is purely probabilistic — failed events are not auto-promoted. If you want to always capture failures and 10% of successes, combine `capture_success=False` with your own filtering, or wrap `capture_event` yourself.

---

## Sensitive Data Masking

The default mask list matches the TS SDK and includes `authorization`, `password`, `token`, `access_token`, `refresh_token`, `apikey`, `api_key`, `secret`, `client_secret`, `cookie`, `set-cookie`, `cardNumber`, `card_number`, `cvv`, `otp`, and others. Add more via `mask_fields`.

```python
replaystack = ReplayStackClient(
    api_key="rs_live_xxx",
    mask_fields=["ssn", "tax_id", "phone_number"],
)
```

Detection is case-insensitive and treats `-`, `_`, and spaces as equivalent.

`Authorization` headers are inspected before masking so the dashboard can show
the auth mode (e.g. `bearer`, `basic`, `api_key`, `cookie`) without ever storing
the raw token.

---

## Offline Queue, Flush, and Graceful Shutdown

When the API is unreachable after retries, the prepared payload is buffered in memory (bounded by `offline_queue_max`). Call `flush()` or `close()` to drain it.

```python
replaystack = ReplayStackClient(
    api_key="rs_live_xxx",
    offline_queue_max=200,
    flush_interval_seconds=10,     # periodic best-effort drain
    on_queue_drop=lambda info: print("dropped", info),
)

# Drain manually (e.g. in a /shutdown handler):
replaystack.flush(timeout_seconds=5)

# Stop the periodic timer and drain one last time:
replaystack.close(timeout_seconds=5)
```

---

## Process Guards (recommended for long-running servers)

Register interpreter-level hooks so unhandled exceptions are captured and the
offline queue is drained when the process exits.

```python
from replaystack_sdk import ReplayStackClient, install_replaystack_process_guards

client = ReplayStackClient(api_key="rs_live_xxx", flush_interval_seconds=10)
unsubscribe = install_replaystack_process_guards(client)

# Later, e.g. in tests:
unsubscribe()
```

`install_replaystack_process_guards` wires up:

- `sys.excepthook` → captures uncaught exceptions
- `sys.unraisablehook` → captures unraisable exceptions (3.8+)
- asyncio loop exception handler → captures unhandled task failures
- `SIGINT`, `SIGTERM`, and `atexit` → call `client.flush()` before exit

Options:

```python
install_replaystack_process_guards(
    client,
    uncaught_exception=True,
    unraisable_exception=True,
    asyncio_unhandled_rejection=True,
    flush_on_shutdown=True,
    shutdown_signals=[signal.SIGINT, signal.SIGTERM],
)
```

---

## FastAPI / Starlette

```python
from fastapi import FastAPI
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.fastapi import FastAPIMiddlewareOptions, setup_fastapi

app = FastAPI()
client = ReplayStackClient(api_key="rs_live_xxx", service_name="fastapi-service")

setup_fastapi(
    app,
    client,
    FastAPIMiddlewareOptions(
        capture_request_body=True,
        capture_response_body=True,
        capture_headers=True,
        ignored_paths=["/internal/*"],
        get_trace_id=lambda req: req.headers.get("x-request-id"),
        should_capture=lambda info: info["statusCode"] != 204,
    ),
)

@app.post("/orders")
async def create_order(payload: dict):
    client.add_breadcrumb("Creating order")
    return {"success": True, "order": payload}
```

The middleware emits an absolute `requestUrl`, sets `x-trace-id` on the response, and adds `started` / `finished` / `exception` breadcrumbs automatically. Streaming responses are not body-captured (a marker payload is sent instead).

---

## Flask

```python
from flask import Flask, jsonify, request
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.flask import FlaskMiddlewareOptions, setup_flask

app = Flask(__name__)
client = ReplayStackClient(api_key="rs_live_xxx", service_name="flask-service")

setup_flask(
    app,
    client,
    FlaskMiddlewareOptions(
        capture_request_body=True,
        capture_response_body=True,
        capture_headers=True,
        ignored_paths=["/internal/*"],
        get_trace_id=lambda req: req.headers.get("x-request-id"),
        should_capture=lambda info: info["statusCode"] != 204,
    ),
)

@app.post("/orders")
def create_order():
    client.add_breadcrumb("Creating order")
    return jsonify({"success": True, "order": request.json})
```

`/health`, `/metrics`, and `/favicon.ico` are ignored by default. The middleware sets `x-trace-id` on the response.

---

## Django

In `settings.py`:

```python
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.django import DjangoMiddlewareOptions

REPLAYSTACK_CLIENT = ReplayStackClient(
    api_key="rs_live_xxx",
    service_name="django-service",
    environment="production",
)

REPLAYSTACK_OPTIONS = DjangoMiddlewareOptions(
    capture_request_body=True,
    capture_response_body=True,
    capture_headers=True,
    ignored_paths=["/internal/*"],
    get_trace_id=lambda req: req.headers.get("x-request-id"),
    should_capture=lambda info: info["statusCode"] != 204,
)

MIDDLEWARE = [
    # ... your security/common middleware ...
    "replaystack_sdk.integrations.django.ReplayStackDjangoMiddleware",
    # ... your custom middleware ...
]
```

---

## Direct API Contract

```http
POST /api/v1/ingest/events
Content-Type: application/json
x-tracereplay-api-key: rs_live_xxxxxxxxx
x-replaystack-api-key: rs_live_xxxxxxxxx
```

Example payload:

```json
{
  "traceId": "f4b1d4e3-…-…",
  "eventType": "api",
  "method": "POST",
  "endpoint": "/orders",
  "requestUrl": "https://api.example.com/orders?ref=email",
  "authMode": "bearer",
  "authScheme": "Bearer",
  "status": "failed",
  "statusCode": 500,
  "executionTimeMs": 120,
  "errorName": "TypeError",
  "errorMessage": "'NoneType' object is not subscriptable",
  "stackTrace": "Traceback...",
  "stackFrames": [
    {
      "functionName": "create_order",
      "fileName": "/app/main.py",
      "lineNumber": 18
    }
  ],
  "serviceName": "order-service",
  "environment": "production",
  "appVersion": "1.0.0",
  "commitHash": "a7f91c"
}
```

---

## Production Notes

- The SDK should never crash the user application. Internal errors are silently swallowed unless `raise_on_error=True` is set.
- Failed network calls re-enter the offline queue and drain on the next `flush()` / `close()` / periodic tick.
- Use `ignored_paths` for `/health`, `/metrics`, `/favicon.ico` (already ignored by integrations) and any other noisy endpoints.
- Use `sample_rate` for high-volume successful events; use `capture_success=False` when you only want errors.
- Sensitive fields are masked **after** auth-mode detection, so `authMode` is always reliable even when the raw header is masked in the captured payload.
- Always call `client.close()` (or wire up `install_replaystack_process_guards`) in long-running workers to avoid losing buffered events on shutdown.
