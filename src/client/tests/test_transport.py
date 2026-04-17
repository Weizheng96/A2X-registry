"""Tests for :mod:`src.client.transport` — error mapping."""

from __future__ import annotations

import httpx
import pytest

from src.client.errors import (
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    ServerError,
    UserConfigServiceImmutableError,
    ValidationError,
)
from src.client.transport import HTTPTransport


def _mock(handler):
    """Wrap a callable into an httpx MockTransport."""
    return httpx.MockTransport(handler)


def _swap_client(transport: HTTPTransport, mock_transport: httpx.MockTransport) -> None:
    """Replace the underlying httpx client with one using the given mock transport."""
    transport._client.close()
    transport._client = httpx.Client(
        base_url=str(transport._client.base_url),
        transport=mock_transport,
    )


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status, expected",
        [
            (404, NotFoundError),
            (400, ValidationError),
            (422, ValidationError),
            (500, ServerError),
            (503, ServerError),
            (418, A2XHTTPError),  # catch-all for other 4xx
        ],
    )
    def test_status_maps_to_exception(self, status, expected):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(lambda req: httpx.Response(status, json={"detail": "err"})))
        with pytest.raises(expected) as exc_info:
            t.request("GET", "api/datasets")
        assert exc_info.value.status_code == status
        t.close()

    def test_user_config_detail_specializes(self):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(
            t,
            _mock(
                lambda req: httpx.Response(
                    400, json={"detail": "Cannot edit user_config-sourced service"}
                )
            ),
        )
        with pytest.raises(UserConfigServiceImmutableError):
            t.request("PUT", "api/datasets/ds/services/sid")
        t.close()

    def test_user_config_is_subclass_of_validation(self):
        # Callers who only `except ValidationError` still catch the specialized one.
        assert issubclass(UserConfigServiceImmutableError, ValidationError)


class TestConnectionFailures:
    def test_connect_error_wrapped(self):
        t = HTTPTransport(base_url="http://x/")

        def raise_connect(req):
            raise httpx.ConnectError("refused")

        _swap_client(t, _mock(raise_connect))
        with pytest.raises(A2XConnectionError):
            t.request("GET", "api/datasets")
        t.close()


class TestResponsePassthrough:
    def test_2xx_returns_response_object(self):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(
            t,
            _mock(lambda req: httpx.Response(200, json={"ok": True})),
        )
        resp = t.request("GET", "api/datasets")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        t.close()

    def test_request_forwards_body_and_params(self):
        import json as _json
        seen = {}

        def capture(req: httpx.Request) -> httpx.Response:
            seen["method"] = req.method
            seen["url"] = str(req.url)
            seen["body"] = req.content
            return httpx.Response(200, json={})

        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(capture))
        t.request("POST", "api/datasets", json={"name": "ds"}, params={"mode": "browse"})
        assert seen["method"] == "POST"
        assert "api/datasets" in seen["url"]
        assert "mode=browse" in seen["url"]
        assert _json.loads(seen["body"]) == {"name": "ds"}
        t.close()
