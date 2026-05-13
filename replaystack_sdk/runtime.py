from __future__ import annotations

import atexit
import logging
import signal
import sys
import threading
from typing import Any, Callable, Iterable, List, Optional

from .client import ReplayStackClient

logger = logging.getLogger("replaystack")


def install_replaystack_process_guards(
    client: ReplayStackClient,
    *,
    uncaught_exception: bool = True,
    unraisable_exception: bool = True,
    asyncio_unhandled_rejection: bool = True,
    flush_on_shutdown: bool = True,
    shutdown_signals: Optional[Iterable[int]] = None,
) -> Callable[[], None]:
    """Hook the interpreter so runtime failures are captured and the offline queue
    is drained on shutdown. Returns an unsubscribe callable.

    Mirrors TS `installReplayStackProcessGuards`. Safe to call multiple times.
    """

    unsubs: List[Callable[[], None]] = []

    if uncaught_exception:
        previous_hook = sys.excepthook

        def hook(exc_type, exc_value, exc_tb):
            try:
                if exc_value is not None:
                    client.capture_exception(
                        exc_value,
                        event_type="custom",
                        endpoint="/runtime/uncaughtException",
                        status_code=500,
                    )
            except Exception:
                logger.debug("uncaught_exception hook failed", exc_info=True)
            previous_hook(exc_type, exc_value, exc_tb)

        sys.excepthook = hook
        unsubs.append(lambda: setattr(sys, "excepthook", previous_hook))

    if unraisable_exception and hasattr(sys, "unraisablehook"):
        previous_unraisable = sys.unraisablehook

        def unraisable(args):
            try:
                exc = args.exc_value if getattr(args, "exc_value", None) is not None else args.exc_type
                if isinstance(exc, BaseException):
                    client.capture_exception(
                        exc,
                        event_type="custom",
                        endpoint="/runtime/unraisable",
                        status_code=500,
                    )
            except Exception:
                logger.debug("unraisable hook failed", exc_info=True)
            previous_unraisable(args)

        sys.unraisablehook = unraisable
        unsubs.append(lambda: setattr(sys, "unraisablehook", previous_unraisable))

    if asyncio_unhandled_rejection:
        unsub = _install_asyncio_handler(client)
        if unsub is not None:
            unsubs.append(unsub)

    if flush_on_shutdown:
        signals_to_register = list(shutdown_signals or (signal.SIGINT, signal.SIGTERM))

        def flush_handler(signum=None, frame=None):
            try:
                client.flush(timeout_seconds=2.0)
            except Exception:
                logger.debug("flush_on_shutdown failed", exc_info=True)

        previous_handlers = {}
        for sig in signals_to_register:
            try:
                # `signal.signal` only works on the main thread; ignore other threads.
                if threading.current_thread() is threading.main_thread():
                    previous_handlers[sig] = signal.getsignal(sig)

                    def handler(signum, frame, _prev=previous_handlers[sig]):
                        flush_handler(signum, frame)
                        if callable(_prev):
                            _prev(signum, frame)

                    signal.signal(sig, handler)
            except (ValueError, OSError):
                logger.debug("signal handler registration failed for %s", sig, exc_info=True)

        atexit.register(flush_handler)
        unsubs.append(lambda: atexit.unregister(flush_handler))

        def _make_signal_restorer(sig_to_restore: int, prev_handler: Any) -> Callable[[], None]:
            def _restore() -> None:
                signal.signal(sig_to_restore, prev_handler)
            return _restore

        for sig, prev in previous_handlers.items():
            unsubs.append(_make_signal_restorer(sig, prev))

    def unsubscribe() -> None:
        for fn in unsubs:
            try:
                fn()
            except Exception:
                logger.debug("guard unsubscribe failed", exc_info=True)

    return unsubscribe


def _install_asyncio_handler(client: ReplayStackClient) -> Optional[Callable[[], None]]:
    try:
        import asyncio
    except ImportError:
        return None

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:
        return None

    if loop is None or loop.is_closed():
        return None

    previous_handler = loop.get_exception_handler()

    def handler(running_loop, context):
        exception = context.get("exception")
        if isinstance(exception, BaseException):
            try:
                client.capture_exception(
                    exception,
                    event_type="custom",
                    endpoint="/runtime/asyncio",
                    status_code=500,
                )
            except Exception:
                logger.debug("asyncio handler failed", exc_info=True)
        if previous_handler is not None:
            previous_handler(running_loop, context)
        else:
            running_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    return lambda: loop.set_exception_handler(previous_handler)
