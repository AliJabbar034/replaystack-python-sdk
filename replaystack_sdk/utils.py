from __future__ import annotations

import copy
import json
import random
import re
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import urlparse

DEFAULT_MASK_FIELDS = {
    "authorization",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "apikey",
    "api_key",
    "secret",
    "client_secret",
    "cookie",
    "set-cookie",
    "cardnumber",
    "card_number",
    "cvv",
    "otp",
}

# Auth header detection — mirrors TS `API_KEY_HEADER_NAMES`.
_API_KEY_HEADER_NAMES = {
    "x-api-key",
    "x-apikey",
    "x-auth-token",
    "x-access-token",
    "x-token",
    "apikey",
    "api-key",
    "authentication",
}

_SESSION_COOKIE_HINTS = (
    "session",
    "sessid",
    "sid=",
    "auth",
    "token",
    "jwt",
    "jsessionid",
    "connect.sid",
)


def create_trace_id() -> str:
    """Generate a UUIDv4 trace id, matching TS `createTraceId`."""

    return str(uuid.uuid4())


def sleep_seconds(seconds: float) -> None:
    """Block the current thread for `seconds`. Useful in retry backoff."""

    time.sleep(max(0.0, seconds))


def normalize_key(key: str) -> str:
    return key.replace("-", "_").replace(" ", "_").lower()


def normalize_endpoint(endpoint: Optional[str]) -> Optional[str]:
    """Return just the path portion of `endpoint`; tolerant to absolute URLs."""

    if not endpoint:
        return endpoint
    try:
        parsed = urlparse(endpoint)
        if parsed.scheme and parsed.netloc:
            return parsed.path or "/"
    except Exception:
        pass
    return endpoint.split("?", 1)[0]


def normalize_absolute_request_url(candidate: Optional[str]) -> Optional[str]:
    """Return canonical absolute http(s) URL or `None` if not absolute."""

    if not candidate:
        return None
    if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        return None
    try:
        parsed = urlparse(candidate)
        if not parsed.scheme or not parsed.netloc:
            return None
        rebuilt = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
        if parsed.query:
            rebuilt += f"?{parsed.query}"
        if parsed.fragment:
            rebuilt += f"#{parsed.fragment}"
        return rebuilt
    except Exception:
        return None


HeaderGetter = Callable[[str], Any]


def _first_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).split(",", 1)[0].strip()


def build_absolute_request_url_from_parts(
    *,
    path_with_query: str,
    get_header: Optional[HeaderGetter] = None,
    protocol_fallback: Optional[str] = None,
) -> Optional[str]:
    """Build `scheme://host/path?query` from a path plus forwarded/Host headers."""

    raw = path_with_query if path_with_query is not None else ""

    if re.match(r"^https?://", raw, flags=re.IGNORECASE):
        return normalize_absolute_request_url(raw) or raw

    if get_header is None:
        return None

    forwarded_host = _first_value(get_header("x-forwarded-host"))
    host = forwarded_host or _first_value(get_header("host"))
    if not host:
        return None

    forwarded_proto = _first_value(get_header("x-forwarded-proto"))
    proto = (forwarded_proto or (protocol_fallback or "http")).rstrip(":")
    path = raw if raw.startswith("/") else f"/{raw}"
    return f"{proto}://{host}{path}"


def truncate_utf8_string(value: str, max_bytes: int) -> str:
    """Trim `value` to fit within `max_bytes` UTF-8 bytes, suffixing with `…`."""

    if not value:
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    end = max_bytes
    while end > 0:
        try:
            slice_str = encoded[:end].decode("utf-8")
        except UnicodeDecodeError:
            end -= 1
            continue
        if len(slice_str.encode("utf-8")) <= max_bytes:
            return f"{slice_str}…"
        end -= 1
    return "…"


def headers_to_object(headers: Any) -> Optional[Dict[str, Any]]:
    """Normalize various header containers into a plain `dict`."""

    if headers is None:
        return None
    if isinstance(headers, dict):
        return dict(headers)
    # Werkzeug / Starlette headers expose `.items()` returning duplicates per key.
    if hasattr(headers, "items"):
        try:
            collected: Dict[str, Any] = {}
            for key, value in headers.items():
                if key in collected:
                    existing = collected[key]
                    if isinstance(existing, list):
                        existing.append(value)
                    else:
                        collected[key] = [existing, value]
                else:
                    collected[key] = value
            return collected
        except Exception:
            return None
    return None


def detect_auth_mode(headers: Any) -> Dict[str, Optional[str]]:
    """Inspect request headers and return `{ "mode": ..., "scheme": ... }`.

    Must be called BEFORE masking so raw header values are still readable;
    no token value is returned, only the auth scheme name.
    """

    flat = _flatten_headers_for_auth_check(headers)
    if not flat:
        return {"mode": "none", "scheme": None}

    authz_raw = flat.get("authorization")
    if authz_raw:
        first = str(authz_raw).strip()
        scheme = first.split(None, 1)[0] if first else ""
        scheme_lower = scheme.lower()
        if scheme_lower == "bearer":
            return {"mode": "bearer", "scheme": scheme}
        if scheme_lower == "basic":
            return {"mode": "basic", "scheme": scheme}
        if scheme_lower in {"token", "apikey", "api-key"}:
            return {"mode": "api_key", "scheme": scheme}
        return {"mode": "other", "scheme": scheme or None}

    for name in _API_KEY_HEADER_NAMES:
        if flat.get(name):
            return {"mode": "api_key", "scheme": name}

    cookie = flat.get("cookie")
    if cookie:
        return {"mode": "cookie", "scheme": None}

    return {"mode": "none", "scheme": None}


def _flatten_headers_for_auth_check(headers: Any) -> Optional[Dict[str, str]]:
    obj = headers_to_object(headers)
    if obj is None:
        return None
    flat: Dict[str, str] = {}
    for key, value in obj.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        flat[str(key).lower()] = str(value)
    return flat


def should_ignore_path(path: Optional[str], ignored_paths: Optional[Iterable[str]] = None) -> bool:
    """Return True if `path` should be filtered. Supports `prefix*` glob suffix."""

    if not path or not ignored_paths:
        return False
    for entry in ignored_paths:
        if not entry:
            continue
        if entry.endswith("*"):
            if path.startswith(entry[:-1]):
                return True
            continue
        if path == entry or path.startswith(entry):
            return True
    return False


def should_sample(sample_rate: float = 1.0) -> bool:
    """Probabilistic sampling. Returns True/False solely from `sample_rate`."""

    if sample_rate >= 1:
        return True
    if sample_rate <= 0:
        return False
    return random.random() <= sample_rate


def mask_sensitive_data(value: Any, custom_fields: Optional[Iterable[str]] = None, replacement: str = "[MASKED]") -> Any:
    """Deep-copy and mask sensitive field values by key name (case-insensitive)."""

    fields = set(DEFAULT_MASK_FIELDS)
    if custom_fields:
        fields.update(normalize_key(f) for f in custom_fields)

    seen: Dict[int, bool] = {}

    def _mask(item: Any) -> Any:
        if item is None:
            return item
        if isinstance(item, dict):
            ident = id(item)
            if seen.get(ident):
                return "[Circular]"
            seen[ident] = True
            out: Dict[str, Any] = {}
            for k, v in item.items():
                if normalize_key(str(k)) in fields:
                    out[k] = replacement
                else:
                    out[k] = _mask(v)
            return out
        if isinstance(item, list):
            ident = id(item)
            if seen.get(ident):
                return "[Circular]"
            seen[ident] = True
            return [_mask(i) for i in item]
        return item

    try:
        cloned = copy.deepcopy(value)
    except Exception:
        cloned = value
    return _mask(cloned)


# Backwards-compatible alias.
mask_data = mask_sensitive_data


def safe_json_clone(value: Any) -> Any:
    """JSON-safe deep clone; replaces circular references with `[Circular]`."""

    if value is None:
        return None

    seen: set = set()

    def _default(obj: Any) -> Any:
        return str(obj)

    def _walk(item: Any) -> Any:
        if isinstance(item, dict):
            ident = id(item)
            if ident in seen:
                return "[Circular]"
            seen.add(ident)
            return {k: _walk(v) for k, v in item.items()}
        if isinstance(item, list):
            ident = id(item)
            if ident in seen:
                return "[Circular]"
            seen.add(ident)
            return [_walk(v) for v in item]
        if isinstance(item, tuple):
            return [_walk(v) for v in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        try:
            json.dumps(item)
            return item
        except Exception:
            return _default(item)

    try:
        return json.loads(json.dumps(_walk(value), default=_default))
    except Exception:
        return str(value)


def truncate_payload(value: Any, max_bytes: int) -> Any:
    """Return `value` unchanged when it fits in `max_bytes`, else a TS-shaped marker."""

    if value is None:
        return value
    if max_bytes <= 0:
        return value
    try:
        raw = json.dumps(value, default=str)
    except Exception:
        return value
    size = len(raw.encode("utf-8"))
    if size <= max_bytes:
        return value
    preview_len = min(max_bytes, 2000)
    return {
        "__truncated": True,
        "originalSizeBytes": size,
        "maxSizeBytes": max_bytes,
        "preview": raw[:preview_len],
    }


def safe_stringify(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def get_error_details(error: Any) -> Dict[str, Any]:
    """Return `errorName`, `errorMessage`, `stackTrace`, `stackFrames` from any error-like."""

    from .stacktrace import parse_exception_stack

    if isinstance(error, BaseException):
        stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        return {
            "errorName": error.__class__.__name__,
            "errorMessage": str(error),
            "stackTrace": stack_trace,
            "stackFrames": parse_exception_stack(error),
        }
    if isinstance(error, str):
        return {
            "errorName": None,
            "errorMessage": error,
            "stackTrace": None,
            "stackFrames": [],
        }
    return {
        "errorName": None,
        "errorMessage": safe_stringify(error),
        "stackTrace": None,
        "stackFrames": [],
    }
