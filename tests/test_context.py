from __future__ import annotations

import threading

from replaystack_sdk import (
    add_breadcrumb,
    clear_breadcrumbs,
    get_breadcrumbs,
    run_with_replaystack_context,
)


def test_run_with_replaystack_context_isolates_breadcrumbs() -> None:
    with run_with_replaystack_context():
        add_breadcrumb("a", category="http")
        add_breadcrumb("b")
        crumbs = get_breadcrumbs()
        assert [c["message"] for c in crumbs] == ["a", "b"]


def test_breadcrumbs_clear() -> None:
    with run_with_replaystack_context():
        add_breadcrumb("only")
        assert len(get_breadcrumbs()) == 1
        clear_breadcrumbs()
        assert get_breadcrumbs() == []


def test_breadcrumbs_are_thread_scoped() -> None:
    other_thread_crumbs: list = []

    def worker() -> None:
        with run_with_replaystack_context():
            add_breadcrumb("worker")
            other_thread_crumbs.extend(get_breadcrumbs())

    with run_with_replaystack_context():
        add_breadcrumb("main")
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert [c["message"] for c in get_breadcrumbs()] == ["main"]

    assert [c["message"] for c in other_thread_crumbs] == ["worker"]
