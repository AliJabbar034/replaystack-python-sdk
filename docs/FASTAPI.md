# ReplayStack FastAPI Integration

Minimal setup:

```python
from fastapi import FastAPI
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.fastapi import setup_fastapi

app = FastAPI()
client = ReplayStackClient(api_key="rs_live_xxx", service_name="fastapi-api")
setup_fastapi(app, client)
```

The middleware captures request method, path, headers, body, response status, response body (for non-streaming responses), execution time, exceptions, parsed stack frames, and breadcrumbs. Each request runs inside its own breadcrumb scope (`run_with_replaystack_context`), and the absolute `requestUrl` is recorded automatically.

## Middleware Options

Pass `FastAPIMiddlewareOptions` to customize capture:

```python
from replaystack_sdk.integrations.fastapi import (
    FastAPIMiddlewareOptions,
    setup_fastapi,
)

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
```

| Option | Default | Effect |
|---|---|---|
| `capture_request_body` | `True` | Read and ship the request body. |
| `capture_response_body` | `True` | Read and ship non-streaming response bodies. |
| `capture_headers` | `True` | Include request + response headers (after masking). |
| `ignored_paths` | `[]` | Path prefixes / `prefix*` globs to skip. Merged with `/health`, `/metrics`, `/favicon.ico`. |
| `get_trace_id` | `None` | Map a request to a custom trace id; falls back to `x-trace-id` then a fresh UUID. |
| `should_capture` | `None` | Predicate `(info) -> bool` to skip individual events. `info` has `method`, `path`, `statusCode`, `executionTimeMs`. |

## Streaming Responses

FastAPI streaming responses are not body-captured (their generator can only be consumed once). The captured event includes a marker payload:

```json
{ "captured": false, "reason": "Streaming response body not captured" }
```

If you need exact response bodies, fall back to `client.capture_event(...)` inside the handler.

## Trace ID Propagation

The middleware writes `x-trace-id` to every response so downstream clients (and the dashboard) can correlate events. If the inbound request already has `x-trace-id`, the same value is reused.

## Custom Exception Handlers

If you register your own FastAPI exception handlers, exceptions thrown by route handlers still reach the middleware first, so the SDK captures them. Then your handler decides what response to return.

## Async Background Tasks

For background tasks (Celery, RQ, asyncio.create_task) use `run_with_replaystack_context` to scope breadcrumbs per task:

```python
from replaystack_sdk import run_with_replaystack_context

async def send_email(user_id: int):
    with run_with_replaystack_context():
        client.add_breadcrumb("email task started")
        ...
```
