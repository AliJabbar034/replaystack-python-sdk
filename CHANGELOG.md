# Changelog

All notable changes to `replaystack-sdk` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/).

## [1.1.3] - 2026-05-15

### Fixed

- PyPI project URLs: Repository, Issues, and Changelog now point to
  `github.com/AliJabbar034/replaystack-python-sdk` (the previous `replaystack/...`
  paths returned 404).
- Documentation URL in package metadata set to `https://www.replaystack.co/docs`.

## [1.1.2] - 2026-05-13

Republish of the 1.1.0 distribution under a new version slot. No functional or
API changes compared to 1.1.0 — this release exists only because PyPI version
slots are immutable and a re-upload of 1.1.0 was needed.

## [1.1.0] - 2026-05-13

Feature-parity release with the official Node.js SDK
([`@replaystack/sdk`](https://www.npmjs.com/package/@replaystack/sdk)).

### Added

- `ReplayStackClient.flush()` and `ReplayStackClient.close()` for graceful
  shutdown and offline-queue draining.
- Bounded in-memory **offline queue** (`offline_queue_max`, default `100`) that
  buffers events when the API is unreachable and drains on the next `flush()`,
  `close()`, or periodic tick.
- **Periodic background flush** via `flush_interval_seconds` (daemon thread,
  no-op when `0`).
- `install_replaystack_process_guards(client, ...)` — registers
  `sys.excepthook`, `sys.unraisablehook`, an asyncio loop exception handler,
  `SIGINT`/`SIGTERM` handlers, and `atexit` to capture unhandled exceptions
  and flush on shutdown.
- `create_trace_id`, `normalize_endpoint`, `normalize_absolute_request_url`,
  `build_absolute_request_url_from_parts`, `truncate_utf8_string`,
  `detect_auth_mode`, `headers_to_object`, `safe_json_clone`, `safe_stringify`,
  `get_error_details`, `sleep_seconds` utilities exported from
  `replaystack_sdk`.
- `run_with_replaystack_context()` context manager and
  `add_context_breadcrumb` / `get_context_breadcrumbs` /
  `clear_context_breadcrumbs` helpers for per-request breadcrumb scoping.
- `ReplayStackConfig`, `ReplayStackLog`, `CaptureResponse`, and Literal type
  aliases (`AuthMode`, `EventStatus`, `EventType`, `LogLevel`).
- `requestUrl`, `authMode`, `authScheme` fields on every captured event.
  Authentication signal is detected from request headers **before** masking,
  so the raw token is never sent.
- Framework integration **options dataclasses**:
  `FastAPIMiddlewareOptions`, `FlaskMiddlewareOptions`, `DjangoMiddlewareOptions`.
  Each supports `capture_request_body`, `capture_response_body`,
  `capture_headers`, `ignored_paths`, `get_trace_id`, `should_capture`.
- All integrations now set `x-trace-id` on the response and emit absolute
  `requestUrl` values.
- `on_error(exception)` and `on_queue_drop(info)` callback hooks on the client.
- PEP 561 `py.typed` marker — type hints ship with the package.

### Changed

- **Default endpoint** is now `https://api.replaystack.co` (was
  `https://api.replaystack.dev`).
- **Default ingest path** is now `/api/v1/ingest/events` (was
  `/api/ingest/events`).
- The canonical API key header sent on every request is now
  `x-tracereplay-api-key` (the previous `x-replaystack-api-key` is still sent
  alongside it as a legacy alias).
- `ReplayStackClient(api_key=None)` now **raises `ValueError`** when no key is
  provided (previously the SDK silently disabled itself). Use
  `enabled=False` to disable the SDK without removing code.
- `should_sample(sample_rate)` is now purely probabilistic. Failed events are
  **no longer auto-promoted** regardless of `sample_rate`. Combine
  `capture_success=False` with your own logic if you only want errors.
- `truncate_payload` now returns the TS-aligned marker shape
  (`__truncated`, `originalSizeBytes`, `maxSizeBytes`, `preview`).
- `should_ignore_path` now supports `prefix*` glob suffixes.
- Default mask list aligned with the TS SDK
  (added `set-cookie`, `client_secret`, `cardNumber`).
- `StackFrame.to_dict()` now emits camelCase keys
  (`functionName`, `fileName`, `lineNumber`, `columnNumber`).
- Stack-trace parser now understands both Python and V8/Node frame formats.
- `SDK_VERSION` is now sourced from package metadata via
  `importlib.metadata.version`, eliminating drift.

### Fixed

- `endpoint` is now normalized before the ignored-path check, so absolute URLs
  passed by integrations are correctly matched against `ignored_paths`.

### Migration notes

- If you relied on the SDK auto-disabling when `api_key` was missing, set
  `enabled=False` explicitly or guard the client construction in your code.
- If you depended on "always capture failed events even at `sample_rate=0`",
  set `capture_success=False` and let sampling apply only to successes, or
  call `capture_event` directly from your error handler.
- If you point at a self-hosted backend, set `endpoint` (or
  `REPLAYSTACK_ENDPOINT`) — the default host changed.
- Replace any code reading `x-replaystack-api-key` on the receiving side with
  also accepting `x-tracereplay-api-key`.

## [1.0.0] - 2026-05-10

Initial public release.

[1.1.3]: https://github.com/AliJabbar034/replaystack-python-sdk/releases/tag/v1.1.3
[1.1.2]: https://github.com/AliJabbar034/replaystack-python-sdk/releases/tag/v1.1.2
[1.1.0]: https://github.com/AliJabbar034/replaystack-python-sdk/releases/tag/v1.1.0
[1.0.0]: https://github.com/AliJabbar034/replaystack-python-sdk/releases/tag/v1.0.0
