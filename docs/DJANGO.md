# ReplayStack Django Integration

In `settings.py`:

```python
from replaystack_sdk import ReplayStackClient
from replaystack_sdk.integrations.django import DjangoMiddlewareOptions

REPLAYSTACK_CLIENT = ReplayStackClient(
    api_key="rs_live_xxx",
    service_name="django-api",
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
    "django.middleware.security.SecurityMiddleware",
    # ... other security/common middleware ...
    "replaystack_sdk.integrations.django.ReplayStackDjangoMiddleware",
    # ... your custom middleware ...
]
```

The middleware captures requests, responses, exceptions, parsed stack frames, and breadcrumbs. Each request runs inside its own breadcrumb scope, and the absolute `requestUrl` is recorded automatically.

## Middleware Options

| Option | Default | Effect |
|---|---|---|
| `capture_request_body` | `True` | Decode and ship the raw request body. |
| `capture_response_body` | `True` | Ship `response.content` (capped at 5 KB). |
| `capture_headers` | `True` | Include request + response headers (after masking). |
| `ignored_paths` | `[]` | Path prefixes / `prefix*` globs to skip. Merged with `/health`, `/metrics`, `/favicon.ico`. |
| `get_trace_id` | `None` | Map a request to a custom trace id; falls back to `x-trace-id` then a fresh UUID. |
| `should_capture` | `None` | Predicate `(info) -> bool` to skip individual events. |

Both `REPLAYSTACK_CLIENT` and `REPLAYSTACK_OPTIONS` are read lazily on first request; if either is missing the middleware falls back to `ReplayStackClient()` and `DjangoMiddlewareOptions()` defaults (which still require `REPLAYSTACK_API_KEY` to be set).

## Trace ID Propagation

The middleware writes `x-trace-id` on every response. If the inbound request already includes `x-trace-id`, the value is reused.

## Middleware Ordering

Place `ReplayStackDjangoMiddleware` after Django's security/common middleware but before your custom view middleware. That way, security checks (CSRF, SecurityMiddleware) still run, but the SDK still sees the unwrapped request body and exception.

## Process Guards for `manage.py` Commands and ASGI Servers

For long-running ASGI servers and management commands, register process guards once at startup so the offline queue is drained when the worker exits:

```python
from replaystack_sdk import install_replaystack_process_guards

install_replaystack_process_guards(REPLAYSTACK_CLIENT)
```
