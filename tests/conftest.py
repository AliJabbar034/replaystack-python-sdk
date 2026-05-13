from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pytest

from replaystack_sdk import ReplayStackClient


class FakeResponse:
    """Minimal `requests.Response` look-alike for the SDK transport."""

    def __init__(self, status_code: int = 200, body: Optional[Dict[str, Any]] = None) -> None:
        self.status_code = status_code
        self._body = json.dumps(body if body is not None else {"success": True}).encode("utf-8")

    @property
    def content(self) -> bytes:
        return self._body

    def json(self) -> Dict[str, Any]:
        return json.loads(self._body.decode("utf-8"))


Behavior = Callable[..., FakeResponse]


@dataclass
class FakeTransport:
    """Records every `.post(...)` call and returns canned responses."""

    behavior: Optional[Behavior] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def post(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.behavior is None:
            return FakeResponse(200, {"success": True, "data": {"eventId": "evt_1"}})
        return self.behavior(url=url, json=json, headers=headers, timeout=timeout)


@pytest.fixture()
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture()
def make_client(transport: FakeTransport):
    """Factory for `ReplayStackClient` pre-wired with the fake transport."""

    def _factory(**overrides: Any) -> ReplayStackClient:
        defaults: Dict[str, Any] = {
            "api_key": "rs_test_key",
            "async_send": False,
            "transport": transport,
            "max_retries": 0,
            "sample_rate": 1.0,
        }
        defaults.update(overrides)
        return ReplayStackClient(**defaults)

    return _factory
