# ReplayStack Runtime Process Guards

For long-running Python processes (Gunicorn, Uvicorn, Celery workers, daemons),
register interpreter-level hooks so unhandled exceptions are captured and the
SDK's offline queue is drained before the process exits.

This is the Python analog of `installReplayStackProcessGuards` in the TS SDK.

```python
from replaystack_sdk import ReplayStackClient, install_replaystack_process_guards

client = ReplayStackClient(
    api_key="rs_live_xxx",
    flush_interval_seconds=10,    # also drain periodically
)

unsubscribe = install_replaystack_process_guards(client)

# Later (e.g. in a test teardown):
unsubscribe()
```

## What gets installed

| Hook | What it captures |
|---|---|
| `sys.excepthook` | Uncaught exceptions on the main thread, captured to `/runtime/uncaughtException` with `statusCode=500`. |
| `sys.unraisablehook` (Python 3.8+) | "Unraisable" exceptions (e.g. during interpreter shutdown), captured to `/runtime/unraisable`. |
| asyncio loop exception handler | Unhandled task / future exceptions, captured to `/runtime/asyncio`. |
| `signal.SIGINT`, `signal.SIGTERM` | Call `client.flush(timeout_seconds=2)` before re-raising the previous handler. |
| `atexit` | Final `client.flush(timeout_seconds=2)` on interpreter exit. |

## Options

```python
import signal
from replaystack_sdk import install_replaystack_process_guards

install_replaystack_process_guards(
    client,
    uncaught_exception=True,
    unraisable_exception=True,
    asyncio_unhandled_rejection=True,
    flush_on_shutdown=True,
    shutdown_signals=[signal.SIGINT, signal.SIGTERM],
)
```

| Argument | Default | Notes |
|---|---|---|
| `uncaught_exception` | `True` | Register `sys.excepthook`. |
| `unraisable_exception` | `True` | Register `sys.unraisablehook` (no-op on 3.7). |
| `asyncio_unhandled_rejection` | `True` | Wire the event loop exception handler. |
| `flush_on_shutdown` | `True` | Register signal handlers and `atexit`. |
| `shutdown_signals` | `(SIGINT, SIGTERM)` | Signals that should trigger a final flush. |

## Notes

- Signal handlers can only be registered from the main thread; the runtime
  module silently skips them in other threads.
- Previous `sys.excepthook` / `sys.unraisablehook` / asyncio handlers are
  preserved and invoked after the SDK is done.
- The returned `unsubscribe()` callable removes every hook the call installed —
  use it in tests to avoid cross-test interference.
- The guards never call `sys.exit()` or `os._exit()`. They only ensure that
  events are captured and flushed; the rest of your shutdown logic continues
  to run.
