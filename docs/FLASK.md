# ReplayStack Flask Integration

Minimal setup:

```python
from flask import Flask
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.flask import setup_flask

app = Flask(__name__)
client = ReplayStackClient(api_key="rs_live_xxx", service_name="flask-api")
setup_flask(app, client)
```

The middleware hooks `before_request`, `after_request`, `teardown_request`, and `errorhandler(Exception)` so request/response data and exceptions are captured automatically. Each request runs inside its own breadcrumb scope, and the absolute `requestUrl` is recorded automatically.

## Middleware Options

```python
from replaystack_sdk.integrations.flask import FlaskMiddlewareOptions, setup_flask

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
```

| Option | Default | Effect |
|---|---|---|
| `capture_request_body` | `True` | Read and ship request body (JSON or text). |
| `capture_response_body` | `True` | Read and ship response body (JSON or text, capped at 5 KB for text). |
| `capture_headers` | `True` | Include request + response headers (after masking). |
| `ignored_paths` | `[]` | Path prefixes / `prefix*` globs to skip. Merged with `/health`, `/metrics`, `/favicon.ico`. |
| `get_trace_id` | `None` | Map a request to a custom trace id; falls back to `x-trace-id` then a fresh UUID. |
| `should_capture` | `None` | Predicate `(info) -> bool` to skip individual events. `info` has `method`, `path`, `statusCode`, `executionTimeMs`. |

## Trace ID Propagation

The middleware writes `x-trace-id` on every response. If the inbound request already includes `x-trace-id`, the value is reused so the SDK and downstream consumers stay correlated.

## Error Handler Ordering

`setup_flask` registers `@app.errorhandler(Exception)`. If you have your own catch-all error handler, register the SDK first (so the SDK sees the exception with its full stack) and re-raise from the SDK handler if needed. The SDK's error handler always re-raises so subsequent Flask handlers still run.

## Custom Workers (Celery, RQ)

For workers/cron jobs use `run_with_replaystack_context` to scope breadcrumbs per task:

```python
from replaystack_sdk import run_with_replaystack_context

def process_order(order_id):
    with run_with_replaystack_context():
        client.add_breadcrumb("order task started")
        ...
```
