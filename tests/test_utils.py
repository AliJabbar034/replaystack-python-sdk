from __future__ import annotations

import pytest

from replaystack_sdk import (
    build_absolute_request_url_from_parts,
    create_trace_id,
    detect_auth_mode,
    get_error_details,
    mask_sensitive_data,
    normalize_absolute_request_url,
    normalize_endpoint,
    safe_json_clone,
    safe_stringify,
    should_ignore_path,
    should_sample,
    truncate_payload,
    truncate_utf8_string,
)


def test_create_trace_id_is_uuid4_shape() -> None:
    tid = create_trace_id()
    assert isinstance(tid, str)
    assert len(tid) == 36
    assert tid.count("-") == 4


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.example.com/users/42?x=1", "/users/42"),
        ("/users/42?x=1", "/users/42"),
        ("", ""),
        (None, None),
    ],
)
def test_normalize_endpoint(value, expected) -> None:
    assert normalize_endpoint(value) == expected


def test_normalize_absolute_request_url() -> None:
    assert normalize_absolute_request_url("https://api.example.com/x?y=1") == "https://api.example.com/x?y=1"
    assert normalize_absolute_request_url("/x") is None
    assert normalize_absolute_request_url("") is None


def test_build_absolute_request_url_from_parts() -> None:
    headers = {"host": "api.example.com", "x-forwarded-proto": "https"}
    url = build_absolute_request_url_from_parts(
        path_with_query="/foo?x=1",
        get_header=lambda name: headers.get(name.lower()),
    )
    assert url == "https://api.example.com/foo?x=1"


def test_build_absolute_request_url_from_parts_returns_none_when_no_host() -> None:
    assert (
        build_absolute_request_url_from_parts(path_with_query="/foo", get_header=lambda _: None) is None
    )


def test_build_absolute_request_url_passthrough_absolute() -> None:
    url = build_absolute_request_url_from_parts(
        path_with_query="https://api.example.com/already?x=1",
        get_header=lambda _: None,
    )
    assert url == "https://api.example.com/already?x=1"


@pytest.mark.parametrize(
    ("path", "ignored", "expected"),
    [
        ("/health", ["/health"], True),
        ("/internal/abc", ["/internal/*"], True),
        ("/v1/users", ["/health"], False),
        ("/users/42", ["/users"], True),
        (None, ["/users"], False),
    ],
)
def test_should_ignore_path(path, ignored, expected) -> None:
    assert should_ignore_path(path, ignored) is expected


def test_should_sample_is_purely_probabilistic() -> None:
    assert should_sample(1.0) is True
    assert should_sample(0.0) is False
    # 0.5 is non-deterministic but must be a bool.
    assert isinstance(should_sample(0.5), bool)


@pytest.mark.parametrize(
    ("headers", "expected_mode", "expected_scheme"),
    [
        ({"Authorization": "Bearer abc"}, "bearer", "Bearer"),
        ({"authorization": "Basic xxxxx"}, "basic", "Basic"),
        ({"Authorization": "Hawk id=..."}, "other", "Hawk"),
        ({"X-Api-Key": "abc"}, "api_key", "x-api-key"),
        ({"Cookie": "session=abc"}, "cookie", None),
        ({}, "none", None),
    ],
)
def test_detect_auth_mode(headers, expected_mode, expected_scheme) -> None:
    detected = detect_auth_mode(headers)
    assert detected["mode"] == expected_mode
    assert detected["scheme"] == expected_scheme


def test_mask_sensitive_data_replaces_known_fields() -> None:
    masked = mask_sensitive_data(
        {"password": "p", "user": {"token": "t", "name": "x"}},
        ["custom"],
    )
    assert masked == {"password": "[MASKED]", "user": {"token": "[MASKED]", "name": "x"}}


def test_mask_sensitive_data_supports_custom_fields() -> None:
    masked = mask_sensitive_data({"phone": "1234"}, custom_fields=["phone"])
    assert masked == {"phone": "[MASKED]"}


def test_truncate_payload_returns_value_when_under_limit() -> None:
    payload = {"data": "x"}
    assert truncate_payload(payload, 4096) is payload


def test_truncate_payload_returns_ts_aligned_marker_when_exceeded() -> None:
    big = {"data": "x" * 2000}
    out = truncate_payload(big, 100)
    assert isinstance(out, dict)
    assert out["__truncated"] is True
    assert out["maxSizeBytes"] == 100
    assert out["originalSizeBytes"] > 100
    assert isinstance(out["preview"], str)


def test_truncate_utf8_string_keeps_short_strings() -> None:
    assert truncate_utf8_string("hello", 100) == "hello"


def test_truncate_utf8_string_trims_long_strings() -> None:
    out = truncate_utf8_string("h" * 200, 50)
    assert out.endswith("…")
    assert len(out.encode("utf-8")) <= 53  # 50 bytes + 3-byte ellipsis


def test_safe_stringify_handles_unserializable() -> None:
    class Custom:
        def __repr__(self) -> str:
            return "<custom>"

    assert "custom" in safe_stringify(Custom()).lower()


def test_safe_json_clone_replaces_cycles() -> None:
    a: dict = {}
    a["self"] = a
    cloned = safe_json_clone(a)
    assert cloned == {"self": "[Circular]"}


def test_get_error_details_from_exception() -> None:
    err = get_error_details(RuntimeError("boom"))
    assert err["errorName"] == "RuntimeError"
    assert err["errorMessage"] == "boom"
    assert isinstance(err["stackFrames"], list)


def test_get_error_details_from_string() -> None:
    err = get_error_details("plain message")
    assert err["errorMessage"] == "plain message"
    assert err["stackFrames"] == []
