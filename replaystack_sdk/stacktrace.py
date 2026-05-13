from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional


def format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def parse_exception_stack(exc: BaseException) -> List[Dict[str, Any]]:
    """Walk a live exception's traceback into structured frames."""

    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    parsed: List[Dict[str, Any]] = []
    for frame in frames:
        parsed.append(
            {
                "functionName": frame.name,
                "fileName": frame.filename,
                "lineNumber": frame.lineno,
                "raw": f'{frame.filename}:{frame.lineno} in {frame.name}',
            }
        )
    return parsed


def parse_stack_trace(stack: Optional[str]) -> List[Dict[str, Any]]:
    """Fallback parser for pre-formatted stack-trace strings.

    Recognises both Python format `File "path", line N, in name` and V8/Node
    `at fn (file:L:C)` / `at file:L:C`. Unknown lines are surfaced as `{ "raw": ... }`.
    """

    if not stack:
        return []
    parsed: List[Dict[str, Any]] = []
    for line in stack.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        frame = _parse_python_frame(trimmed)
        if frame is not None:
            parsed.append(frame)
            continue
        frame = _parse_v8_frame(trimmed)
        if frame is not None:
            parsed.append(frame)
            continue
        parsed.append({"raw": trimmed})
    return parsed


def _parse_python_frame(line: str) -> Optional[Dict[str, Any]]:
    if not line.startswith('File "'):
        return None
    try:
        file_part = line.split('"')[1]
        after = line.split("line ", 1)[1]
        line_no = int(after.split(",", 1)[0])
        function_name = after.split("in ", 1)[1] if "in " in after else None
        return {
            "functionName": function_name,
            "fileName": file_part,
            "lineNumber": line_no,
            "raw": line,
        }
    except Exception:
        return {"raw": line}


def _parse_v8_frame(line: str) -> Optional[Dict[str, Any]]:
    if not line.startswith("at "):
        return None
    body = line[3:]
    if body.startswith("async "):
        body = body[6:]

    # at name (file:line:col)
    if body.endswith(")") and "(" in body:
        name, _, location = body.rpartition(" (")
        location = location[:-1]
        line_no, column_no, file_name = _split_location(location)
        if line_no is not None:
            return {
                "functionName": name.strip() or None,
                "fileName": file_name,
                "lineNumber": line_no,
                "columnNumber": column_no,
                "raw": line,
            }

    # at file:line:col
    line_no, column_no, file_name = _split_location(body)
    if line_no is not None:
        return {
            "fileName": file_name,
            "lineNumber": line_no,
            "columnNumber": column_no,
            "raw": line,
        }

    return {"raw": line}


def _split_location(value: str):
    parts = value.rsplit(":", 2)
    if len(parts) != 3:
        return None, None, None
    file_name, line_str, col_str = parts
    try:
        return int(line_str), int(col_str), file_name
    except ValueError:
        return None, None, None
