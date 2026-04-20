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
    """Replace the underlying httpx client, preserving default headers from init."""
    headers = dict(transport._client.headers)
    transport._client.close()
    transport._client = httpx.Client(
        base_url=str(transport._client.base_url),
        headers=headers,
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


class TestHeaders:
    def test_api_key_becomes_bearer_header(self):
        seen = {}

        def capture(req):
            seen["auth"] = req.headers.get("authorization")
            return httpx.Response(200, json={})

        t = HTTPTransport(base_url="http://x/", headers={"Authorization": "Bearer secret-abc"})
        _swap_client(t, _mock(capture))
        t.request("GET", "foo")
        assert seen["auth"] == "Bearer secret-abc"
        t.close()

    def test_no_headers_means_no_auth(self):
        seen = {}

        def capture(req):
            seen["auth"] = req.headers.get("authorization")
            return httpx.Response(200, json={})

        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(capture))
        t.request("GET", "foo")
        assert seen["auth"] is None
        t.close()


class TestTimeout:
    def test_connect_timeout_wrapped_as_connection_error(self):
        t = HTTPTransport(base_url="http://x/")

        def raise_timeout(req):
            raise httpx.ConnectTimeout("slow")

        _swap_client(t, _mock(raise_timeout))
        with pytest.raises(A2XConnectionError):
            t.request("GET", "foo")
        t.close()

    def test_read_timeout_wrapped(self):
        t = HTTPTransport(base_url="http://x/")

        def raise_timeout(req):
            raise httpx.ReadTimeout("slow read")

        _swap_client(t, _mock(raise_timeout))
        with pytest.raises(A2XConnectionError):
            t.request("GET", "foo")
        t.close()

    def test_timeout_value_propagated_to_underlying_client(self):
        custom = 7.25
        t = HTTPTransport(base_url="http://x/", timeout=custom)
        # httpx stores a Timeout instance; its .connect / .read / .write are all ``custom``.
        assert t._client.timeout.connect == custom
        assert t._client.timeout.read == custom
        t.close()


class TestCloseBehavior:
    def test_close_idempotent_and_marks_client_closed(self):
        t = HTTPTransport(base_url="http://x/")
        assert t._client.is_closed is False
        t.close()
        assert t._client.is_closed is True
        t.close()  # second close must not raise

    def test_context_manager_closes_transport(self):
        with HTTPTransport(base_url="http://x/") as t:
            inner = t._client
            assert inner.is_closed is False
        assert inner.is_closed is True


class TestNonJsonErrorBody:
    """Server-side 4xx/5xx with non-JSON body must not crash the error mapper."""

    def test_html_body_does_not_crash(self):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(
            lambda req: httpx.Response(
                503,
                content=b"<html>service unavailable</html>",
                headers={"content-type": "text/html"},
            )
        ))
        with pytest.raises(ServerError) as exc_info:
            t.request("GET", "foo")
        assert exc_info.value.payload is None
        assert exc_info.value.status_code == 503
        t.close()

    def test_plain_text_body_does_not_crash(self):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(
            lambda req: httpx.Response(
                400, content=b"Bad Request",
                headers={"content-type": "text/plain"},
            )
        ))
        with pytest.raises(ValidationError) as exc_info:
            t.request("GET", "foo")
        assert exc_info.value.status_code == 400
        t.close()

    def test_empty_body_on_error(self):
        t = HTTPTransport(base_url="http://x/")
        _swap_client(t, _mock(lambda req: httpx.Response(500, content=b"")))
        with pytest.raises(ServerError):
            t.request("GET", "foo")
        t.close()
