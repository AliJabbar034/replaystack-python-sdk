from __future__ import annotations

from replaystack_sdk import format_exception, parse_exception_stack, parse_stack_trace


def test_parse_exception_stack_returns_camel_case_frames() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        frames = parse_exception_stack(exc)
    assert frames, "expected at least one frame"
    top = frames[-1]
    assert top["functionName"]
    assert top["fileName"].endswith(".py")
    assert isinstance(top["lineNumber"], int)
    assert "raw" in top


def test_format_exception_contains_message() -> None:
    try:
        raise ValueError("kaboom")
    except ValueError as exc:
        text = format_exception(exc)
    assert "ValueError" in text
    assert "kaboom" in text


def test_parse_stack_trace_handles_python_format() -> None:
    sample = (
        'Traceback (most recent call last):\n'
        '  File "/app/main.py", line 18, in create_order\n'
        '    do_thing()\n'
        '  File "/app/lib.py", line 42, in do_thing\n'
        '    raise RuntimeError("boom")\n'
    )
    frames = parse_stack_trace(sample)
    assert any(f.get("fileName", "").endswith("main.py") for f in frames)
    assert any(f.get("functionName") == "create_order" for f in frames)


def test_parse_stack_trace_handles_v8_format() -> None:
    sample = (
        "Error: boom\n"
        "    at createOrder (/app/src/controllers/order.controller.ts:42:15)\n"
        "    at /app/src/services/order.service.ts:88:9\n"
        "    at async processOrder (/app/src/services/order.service.ts:88:9)"
    )
    frames = parse_stack_trace(sample)
    assert any(f.get("fileName", "").endswith("order.controller.ts") for f in frames)
    assert any(f.get("columnNumber") == 15 for f in frames)
    # The "async" prefix should still parse.
    assert any(f.get("functionName") == "processOrder" for f in frames)


def test_parse_stack_trace_handles_empty() -> None:
    assert parse_stack_trace(None) == []
    assert parse_stack_trace("") == []
