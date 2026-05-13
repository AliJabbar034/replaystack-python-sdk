# ReplayStack Python SDK — Examples

Runnable mini apps that wire up the ReplayStack SDK end-to-end.

These files are part of the GitHub repo only; they are **not** installed when you `pip install replaystack-sdk`.

## Setup

```bash
# From the repo root
pip install -e '.[all]'
export REPLAYSTACK_API_KEY=rs_live_xxxxxxxxxxxxxxxxx
# optional, to point at a self-hosted backend:
export REPLAYSTACK_ENDPOINT=https://api.your-host.com
```

## Apps

### `flask_app.py`

Minimal Flask app that:

- registers the SDK middleware with `FlaskMiddlewareOptions(ignored_paths=["/internal/*"], …)`
- runs the per-request breadcrumb scope automatically
- installs `install_replaystack_process_guards` so unhandled exceptions are captured and the offline queue is flushed on shutdown
- exposes a `/boom` route that raises so you can see end-to-end exception capture

Run it:

```bash
flask --app examples.flask_app run --debug
```

### `fastapi_app.py`

Same flow as the Flask example, but for FastAPI/Starlette:

- registers `FastAPIMiddlewareOptions(ignored_paths=["/internal/*"], …)`
- uses the modern `lifespan` context manager to install process guards on startup and `client.close()` on shutdown
- exposes `/health`, `/orders`, and `/boom`

Run it:

```bash
uvicorn examples.fastapi_app:app --reload
```

## What to look for in the ReplayStack dashboard

After hitting an example route, each captured event should show:

- `traceId` (echoed back to the client via the `x-trace-id` response header)
- `requestUrl` populated as an absolute `https://…` URL
- `authMode` set to `bearer` / `api_key` / `cookie` / `none` if the request was authenticated
- Masked sensitive headers and request payload fields
- Breadcrumbs in order: `HTTP request started` → user-added breadcrumbs → `HTTP request finished` (or `Unhandled exception captured`)

If nothing arrives, check:

1. `REPLAYSTACK_API_KEY` is set and valid.
2. `REPLAYSTACK_ENDPOINT` points at the right host.
3. The route you hit isn't in `ignored_paths` (the integrations also ignore `/health`, `/metrics`, `/favicon.ico` by default).
