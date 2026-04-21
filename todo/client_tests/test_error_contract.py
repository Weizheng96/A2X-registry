"""Error-contract matrix — one-stop reference for "given X, expect Y".

For every public ``A2XClient`` method, this file enumerates:
  1. Local fail-fast validation (ValueError / NotOwnedError, 0 HTTP calls)
  2. HTTP status-code → SDK exception mapping
  3. Connection-layer failures → A2XConnectionError

Each case asserts the exact exception class (not just "some A2XError"),
plus whether an HTTP call was sent. Existing tests in test_client.py /
test_transport.py / test_team_agents.py cover many of these individually;
this file is the consolidated contract view.
"""

from __future__ import annotations

import httpx
import pytest

from src.client import (
    A2XClient,
    A2XConnectionError,
    NotFoundError,
    NotOwnedError,
    ServerError,
    UnexpectedServiceTypeError,
    UserConfigServiceImmutableError,
    ValidationError,
)


# ── Test harness ─────────────────────────────────────────────────────────────

def _mk_client(handler, tmp_path) -> tuple[A2XClient, list[httpx.Request]]:
    """A2XClient wired to an httpx MockTransport; returns also the recorded reqs."""
    recorded: list[httpx.Request] = []

    def wrapper(req):
        recorded.append(req)
        return handler(req)

    client = A2XClient(base_url="http://test", ownership_file=tmp_path / "owned.json")
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(wrapper)
    )
    return client, recorded


def _deny_all(req):
    """Default handler: test expects NO HTTP to be sent; fail loudly if it is."""
    raise AssertionError(f"HTTP should not have been sent: {req.method} {req.url}")


def _static_response(status: int, body: dict | None = None):
    def handler(req):
        return httpx.Response(status, json=body or {})
    return handler


def _raise_transport_exc(exc):
    def handler(req):
        raise exc
    return handler


# ════════════════════════════════════════════════════════════════════════════
# PART 1: Local fail-fast validation (no HTTP)
# ════════════════════════════════════════════════════════════════════════════

class TestLocalValidationSetTeamCount:
    """build_team_count_body rejects bad counts before HTTP."""

    @pytest.mark.parametrize("bad", [-1, True, False, 1.5, "3", None, [], {}])
    def test_bad_count_raises_ValueError(self, tmp_path, bad):
        client, sent = _mk_client(_deny_all, tmp_path)
        client._owned.add("ds", "sid")  # even owned, count check fires first
        with pytest.raises(ValueError, match=r"non-negative int"):
            client.set_team_count("ds", "sid", bad)
        assert len(sent) == 0
        client.close()

    def test_bad_count_precedes_ownership_check(self, tmp_path):
        """Count validated BEFORE _assert_owned — regardless of ownership."""
        client, sent = _mk_client(_deny_all, tmp_path)
        # Foreign sid + bad count → ValueError wins (not NotOwnedError)
        with pytest.raises(ValueError):
            client.set_team_count("ds", "foreign", -1)
        assert len(sent) == 0
        client.close()


class TestLocalValidationRegisterBlankAgent:
    """build_blank_agent_card rejects bad endpoints."""

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 42, 1.5, True, [], {}])
    def test_bad_endpoint_raises_ValueError(self, tmp_path, bad):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError, match=r"endpoint must be a non-empty"):
            client.register_blank_agent("ds", endpoint=bad)
        assert len(sent) == 0
        client.close()


class TestLocalValidationListIdle:
    """n must be a non-negative int."""

    @pytest.mark.parametrize("bad", [-1, -100, True, False, 1.5, "3", None, []])
    def test_bad_n_raises_ValueError(self, tmp_path, bad):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError, match=r"non-negative int"):
            client.list_idle_blank_agents("ds", bad)
        assert len(sent) == 0
        client.close()


class TestLocalValidationListAgents:
    """Filter keys must be non-reserved, non-empty strings; values non-None."""

    @pytest.mark.parametrize("reserved", ["mode", "service_id", "size", "page"])
    def test_reserved_filter_key_rejected(self, tmp_path, reserved):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError, match=r"collides with a reserved"):
            client.list_agents("ds", **{reserved: "x"})
        assert len(sent) == 0
        client.close()

    def test_none_filter_value_rejected(self, tmp_path):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError, match=r"must not be None"):
            client.list_agents("ds", key="ok", bad=None)
        assert len(sent) == 0
        client.close()

    def test_empty_string_key_rejected(self, tmp_path):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError, match=r"filter keys must be non-empty"):
            client.list_agents("ds", **{"": "x"})
        assert len(sent) == 0
        client.close()


class TestLocalValidationReplaceAgentCard:
    """assert_card_has_endpoint rejects cards missing/invalid endpoint."""

    @pytest.mark.parametrize("bad_card", [
        None,                                  # not a dict
        [],                                    # not a dict
        "card",                                # not a dict
        42,                                    # not a dict
        {},                                    # dict but no endpoint key
        {"name": "x", "description": "y"},     # no endpoint key
        {"endpoint": None},                    # None value
        {"endpoint": ""},                      # empty string
        {"endpoint": "   "},                   # whitespace only
        {"endpoint": "\t\n"},                  # whitespace only
        {"endpoint": 42},                      # non-string
        {"endpoint": ["a"]},                   # non-string
        {"endpoint": {"url": "x"}},            # non-string
    ])
    def test_bad_card_raises_ValueError(self, tmp_path, bad_card):
        client, sent = _mk_client(_deny_all, tmp_path)
        # Pre-register so the sid is owned — endpoint check should still win
        client._owned.add("ds", "sid")
        with pytest.raises(ValueError, match=r"endpoint"):
            client.replace_agent_card("ds", "sid", bad_card)  # type: ignore[arg-type]
        assert len(sent) == 0
        client.close()

    def test_endpoint_check_precedes_ownership(self, tmp_path):
        """Card validation fires before ownership — even for foreign sid."""
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError):
            client.replace_agent_card("ds", "foreign", {"name": "x"})
        assert len(sent) == 0
        client.close()

    def test_valid_card_foreign_sid_raises_NotOwnedError(self, tmp_path):
        """With endpoint present, ownership check becomes the failing one."""
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(NotOwnedError):
            client.replace_agent_card(
                "ds", "foreign",
                {"name": "x", "description": "y", "endpoint": "http://e"},
            )
        assert len(sent) == 0
        client.close()


class TestLocalValidationOwnership:
    """update/set_team_count/deregister/restore_to_blank all require ownership."""

    @pytest.mark.parametrize("method_call", [
        lambda c: c.update_agent("ds", "foreign", {"description": "x"}),
        lambda c: c.set_team_count("ds", "foreign", 0),
        lambda c: c.deregister_agent("ds", "foreign"),
        lambda c: c.restore_to_blank("ds", "foreign"),
    ])
    def test_foreign_sid_raises_NotOwnedError_no_http(self, tmp_path, method_call):
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(NotOwnedError) as exc_info:
            method_call(client)
        assert exc_info.value.dataset == "ds"
        assert exc_info.value.service_id == "foreign"
        assert exc_info.value.status_code is None
        assert len(sent) == 0
        client.close()


# ════════════════════════════════════════════════════════════════════════════
# PART 2: HTTP status code → SDK exception mapping (end-to-end via client)
# ════════════════════════════════════════════════════════════════════════════

class TestHttpStatusMapping:
    """Backend responds with error status → SDK surfaces the matching exception."""

    def test_404_raises_NotFoundError(self, tmp_path):
        handler = _static_response(404, {"detail": "not found"})
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(NotFoundError) as exc_info:
            client.get_agent("ds", "missing")
        assert exc_info.value.status_code == 404
        client.close()

    def test_400_raises_ValidationError(self, tmp_path):
        handler = _static_response(400, {"detail": "bad request"})
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(ValidationError) as exc_info:
            client.create_dataset("ds")
        assert exc_info.value.status_code == 400
        client.close()

    def test_422_raises_ValidationError(self, tmp_path):
        handler = _static_response(422, {"detail": "unprocessable"})
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(ValidationError):
            client.create_dataset("ds")
        client.close()

    def test_500_raises_ServerError(self, tmp_path):
        handler = _static_response(500, {"detail": "oops"})
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(ServerError) as exc_info:
            client.list_agents("ds")
        assert exc_info.value.status_code == 500
        client.close()

    def test_503_raises_ServerError(self, tmp_path):
        handler = _static_response(503, {"detail": "unavailable"})
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(ServerError):
            client.list_agents("ds")
        client.close()

    def test_user_config_detail_specializes(self, tmp_path):
        """400 with 'user_config' in detail → UserConfigServiceImmutableError."""
        handler = _static_response(400, {
            "detail": "Cannot update user_config-sourced service",
        })
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid")
        with pytest.raises(UserConfigServiceImmutableError) as exc_info:
            client.update_agent("ds", "sid", {"description": "x"})
        # Still a ValidationError subclass — callers catching ValidationError still work
        assert isinstance(exc_info.value, ValidationError)
        assert exc_info.value.status_code == 400
        client.close()


# ── Connection-layer failures ────────────────────────────────────────────────

class TestConnectionFailures:
    """All httpx transport exceptions funnel into A2XConnectionError."""

    @pytest.mark.parametrize("exc", [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow to connect"),
        httpx.ReadTimeout("slow response"),
        httpx.WriteTimeout("slow send"),
        httpx.PoolTimeout("pool full"),
        httpx.RemoteProtocolError("bad protocol"),
        httpx.ReadError("read failed"),
    ])
    def test_transport_exc_maps_to_A2XConnectionError(self, tmp_path, exc):
        client, _ = _mk_client(_raise_transport_exc(exc), tmp_path)
        with pytest.raises(A2XConnectionError) as exc_info:
            client.list_agents("ds")
        assert exc_info.value.status_code is None
        # Message format: "<httpx type name>: <exc message>"
        assert type(exc).__name__ in str(exc_info.value)
        client.close()


# ════════════════════════════════════════════════════════════════════════════
# PART 3: Special error paths (type / source specific)
# ════════════════════════════════════════════════════════════════════════════

class TestSpecialPaths:
    def test_get_agent_skill_returns_ZIP_raises_UnexpectedServiceTypeError(self, tmp_path):
        """get_agent on a skill service hits application/zip → special error."""
        def handler(req):
            return httpx.Response(
                200,
                content=b"PK\x03\x04zipbytes",
                headers={"content-type": "application/zip"},
            )
        client, _ = _mk_client(handler, tmp_path)
        with pytest.raises(UnexpectedServiceTypeError):
            client.get_agent("ds", "skill_sid")
        client.close()

    def test_get_agent_json_with_charset_succeeds(self, tmp_path):
        """application/json; charset=utf-8 still recognised as JSON (not zip)."""
        def handler(req):
            return httpx.Response(
                200,
                json={"id": "s", "type": "a2a", "name": "n",
                      "description": "d", "metadata": {}},
                headers={"content-type": "application/json; charset=utf-8"},
            )
        client, _ = _mk_client(handler, tmp_path)
        d = client.get_agent("ds", "s")
        assert d.id == "s"
        client.close()

    def test_delete_dataset_400_still_clears_local(self, tmp_path):
        """Deleting a dataset that's gone on server → still scrub local _owned."""
        handler = _static_response(400, {"detail": "dataset does not exist"})
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid1")
        client._owned.add("ds", "sid2")
        with pytest.raises(ValidationError):
            client.delete_dataset("ds")
        # Local sid mapping wiped despite the error
        assert not client._owned.contains("ds", "sid1")
        assert not client._owned.contains("ds", "sid2")
        client.close()

    def test_deregister_404_clears_local(self, tmp_path):
        handler = _static_response(404, {"detail": "not found"})
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid")
        with pytest.raises(NotFoundError):
            client.deregister_agent("ds", "sid")
        assert not client._owned.contains("ds", "sid")
        client.close()

    def test_update_agent_404_clears_local(self, tmp_path):
        handler = _static_response(404, {"detail": "not found"})
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid")
        with pytest.raises(NotFoundError):
            client.update_agent("ds", "sid", {"description": "x"})
        assert not client._owned.contains("ds", "sid")
        client.close()

    def test_set_team_count_404_clears_local(self, tmp_path):
        handler = _static_response(404, {"detail": "not found"})
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid")
        with pytest.raises(NotFoundError):
            client.set_team_count("ds", "sid", 0)
        assert not client._owned.contains("ds", "sid")
        client.close()

    def test_replace_404_clears_local_and_blank_cache(self, tmp_path):
        handler = _static_response(404, {"detail": "not found"})
        client, _ = _mk_client(handler, tmp_path)
        client._owned.add("ds", "sid")
        client._blank_endpoints[("ds", "sid")] = "http://a"
        with pytest.raises(NotFoundError):
            client.replace_agent_card(
                "ds", "sid",
                {"name": "n", "description": "d", "endpoint": "http://a"},
            )
        assert not client._owned.contains("ds", "sid")
        assert ("ds", "sid") not in client._blank_endpoints
        client.close()


# ════════════════════════════════════════════════════════════════════════════
# PART 4: Constructor (__init__) — type safety
# ════════════════════════════════════════════════════════════════════════════

class TestConstructor:
    def test_ownership_file_unsupported_type_raises_TypeError(self, tmp_path):
        with pytest.raises(TypeError):
            A2XClient(base_url="http://t", ownership_file=42)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            A2XClient(base_url="http://t", ownership_file=[])  # type: ignore[arg-type]

    def test_constructor_with_bad_base_url_does_NOT_send_http(self, tmp_path):
        """Per design: __init__ never sends HTTP — bad URL surfaces at first call."""
        client = A2XClient(
            base_url="http://bad-ip-never-reachable:9999",
            timeout=1.0,
            ownership_file=tmp_path / "x.json",
        )
        # Constructor completes fine; bad URL isn't validated until a request
        assert client.base_url == "http://bad-ip-never-reachable:9999/"
        client.close()


# ════════════════════════════════════════════════════════════════════════════
# PART 5: Priority — which check fires first when multiple could
# ════════════════════════════════════════════════════════════════════════════

class TestValidationOrdering:
    """Document the precedence when several validation checks could fire."""

    def test_replace_card_endpoint_check_precedes_ownership(self, tmp_path):
        """Endpoint validation runs before ownership — card without endpoint
        + foreign sid → ValueError (not NotOwnedError)."""
        client, sent = _mk_client(_deny_all, tmp_path)
        with pytest.raises(ValueError):
            client.replace_agent_card("ds", "foreign", {"name": "x"})
        assert len(sent) == 0
        client.close()

    def test_set_team_count_count_check_precedes_ownership(self, tmp_path):
        client, sent = _mk_client(_deny_all, tmp_path)
        # Foreign sid + bad count → ValueError from count validation, not NotOwnedError
        with pytest.raises(ValueError):
            client.set_team_count("ds", "foreign", -1)
        assert len(sent) == 0
        client.close()

    def test_list_idle_blank_agents_n_check_before_http(self, tmp_path):
        """n=0 short-circuits; n<0 raises — neither sends HTTP."""
        client, sent = _mk_client(_deny_all, tmp_path)
        assert client.list_idle_blank_agents("ds", 0) == []
        with pytest.raises(ValueError):
            client.list_idle_blank_agents("ds", -5)
        assert len(sent) == 0
        client.close()


# ════════════════════════════════════════════════════════════════════════════
# PART 6: Confirm NotOwnedError carries context (for logging / alerting)
# ════════════════════════════════════════════════════════════════════════════

class TestNotOwnedErrorContext:
    def test_error_exposes_dataset_and_sid(self, tmp_path):
        client, _ = _mk_client(_deny_all, tmp_path)
        try:
            client.update_agent("my_ds", "sid_xyz", {"description": "x"})
        except NotOwnedError as e:
            assert e.dataset == "my_ds"
            assert e.service_id == "sid_xyz"
            assert "my_ds" in str(e)
            assert "sid_xyz" in str(e)
            assert e.status_code is None
            assert e.payload is None
        client.close()
