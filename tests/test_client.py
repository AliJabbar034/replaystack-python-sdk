from __future__ import annotations

import time
from typing import Any

import pytest

from replaystack_sdk import ReplayStackClient

from .conftest import FakeResponse, FakeTransport


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError, match="apiKey"):
        ReplayStackClient()


def test_sync_capture_event_sends_canonical_headers_and_path(make_client, transport: FakeTransport) -> None:
    client = make_client()
    response = client.capture_event(
        event_type="api",
        method="GET",
        endpoint="https://api.example.com/items/1?x=1",
        request_headers={"Authorization": "Bearer abc"},
        request_payload={"password": "hidden", "ok": True},
        status="success",
        status_code=200,
    )
    assert response is not None and response.success

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"].endswith("/api/v1/ingest/events")
    assert call["headers"]["x-tracereplay-api-key"] == "rs_test_key"
    assert call["headers"]["x-replaystack-api-key"] == "rs_test_key"
    assert call["headers"]["x-replaystack-sdk"] == "python"

    payload = call["json"]
    assert payload["authMode"] == "bearer"
    assert payload["authScheme"] == "Bearer"
    assert payload["requestPayload"]["password"] == "[MASKED]"
    assert payload["endpoint"] == "/items/1"
    assert payload["traceId"]


def test_capture_event_honors_capture_success_flag(make_client, transport: FakeTransport) -> None:
    client = make_client(capture_success=False)

    client.capture_event(event_type="api", endpoint="/x", status="success", status_code=200)
    assert transport.calls == []

    client.capture_event(event_type="api", endpoint="/x", status="failed", status_code=500)
    assert len(transport.calls) == 1


def test_capture_event_respects_ignored_paths(make_client, transport: FakeTransport) -> None:
    client = make_client(ignored_paths=["/internal/*"])
    client.capture_event(event_type="api", endpoint="/internal/secret", status="success")
    assert transport.calls == []
    client.capture_event(event_type="api", endpoint="/public", status="success")
    assert len(transport.calls) == 1


def test_capture_event_skips_when_sample_rate_zero(make_client, transport: FakeTransport) -> None:
    client = make_client(sample_rate=0.0)
    client.capture_event(event_type="api", endpoint="/x", status="failed", status_code=500)
    assert transport.calls == []


def test_capture_event_skips_when_disabled(make_client, transport: FakeTransport) -> None:
    client = make_client(enabled=False)
    client.capture_event(event_type="api", endpoint="/x", status="success")
    assert transport.calls == []


def test_capture_exception_populates_error_fields(make_client, transport: FakeTransport) -> None:
    client = make_client()
    try:
        raise ValueError("kaboom")
    except ValueError as exc:
        client.capture_exception(exc, endpoint="/runtime/test")

    payload = transport.calls[0]["json"]
    assert payload["status"] == "failed"
    assert payload["statusCode"] == 500
    assert payload["errorName"] == "ValueError"
    assert payload["errorMessage"] == "kaboom"
    assert isinstance(payload["stackFrames"], list)
    assert len(payload["stackFrames"]) >= 1


def test_offline_queue_buffers_when_transport_fails_then_drains(make_client) -> None:
    state = {"n": 0}

    def behavior(**_: Any) -> FakeResponse:
        state["n"] += 1
        if state["n"] <= 2:
            raise ConnectionError("offline")
        return FakeResponse(200, {"success": True})

    transport = FakeTransport(behavior=behavior)
    errors: list = []
    client = ReplayStackClient(
        api_key="rs_test_key",
        async_send=False,
        transport=transport,
        max_retries=0,
        offline_queue_max=10,
        on_error=lambda exc: errors.append(exc),
    )

    res = client.capture_event(event_type="api", endpoint="/a", status="failed", status_code=500)
    assert res is None
    assert len(client._offline_queue) == 1

    client.capture_event(event_type="api", endpoint="/b", status="failed", status_code=500)
    assert len(client._offline_queue) == 2

    client.flush()
    assert len(client._offline_queue) == 0
    assert len(errors) == 2  # both initial sends reported failures


def test_offline_queue_drops_oldest_when_full() -> None:
    drops: list = []

    def always_fail(**_: Any) -> FakeResponse:
        raise ConnectionError("nope")

    transport = FakeTransport(behavior=always_fail)
    client = ReplayStackClient(
        api_key="rs_test_key",
        async_send=False,
        transport=transport,
        max_retries=0,
        offline_queue_max=2,
        on_queue_drop=lambda info: drops.append(info),
    )

    for i in range(5):
        client.capture_event(event_type="api", endpoint=f"/x{i}", status="failed", status_code=500)

    assert len(client._offline_queue) == 2
    assert len(drops) == 3
    assert all(info["reason"] == "max_queue_size" for info in drops)


def test_close_stops_periodic_flush_timer() -> None:
    client = ReplayStackClient(
        api_key="rs_test_key",
        async_send=False,
        transport=FakeTransport(),
        flush_interval_seconds=0.05,
    )
    assert client._flush_timer is not None
    time.sleep(0.12)
    client.close()
    assert client._flush_timer is None


def test_close_drains_queue() -> None:
    state = {"n": 0}

    def behavior(**_: Any) -> FakeResponse:
        state["n"] += 1
        if state["n"] <= 1:
            raise ConnectionError("offline")
        return FakeResponse(200, {"success": True})

    transport = FakeTransport(behavior=behavior)
    client = ReplayStackClient(
        api_key="rs_test_key",
        async_send=False,
        transport=transport,
        max_retries=0,
        offline_queue_max=10,
    )
    client.capture_event(event_type="api", endpoint="/a", status="failed", status_code=500)
    assert len(client._offline_queue) == 1
    client.close()
    assert len(client._offline_queue) == 0


def test_raise_on_error_propagates_last_error() -> None:
    def always_fail(**_: Any) -> FakeResponse:
        raise ConnectionError("nope")

    client = ReplayStackClient(
        api_key="rs_test_key",
        async_send=False,
        transport=FakeTransport(behavior=always_fail),
        max_retries=0,
        offline_queue_max=0,
        raise_on_error=True,
    )
    with pytest.raises(ConnectionError):
        client.capture_event(event_type="api", endpoint="/a", status="failed", status_code=500)
