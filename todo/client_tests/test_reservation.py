"""SDK-side tests for the reservation methods.

Covers:
  - reserve_blank_agents — request shape, default filter, holder_id handling
  - release_reservation — bulk + per-sid paths
  - extend_reservation — request shape
  - release_my_lease — happy path, idempotent, ownership guard (no HTTP)
  - replace_agent_card auto-hook — calls release_my_lease, swallowing failures
  - Reservation context manager — auto-release on exit, idempotent

Sync + async are both exercised so the two clients stay symmetric.
"""

from __future__ import annotations

import json
import warnings

import httpx
import pytest

from src.client import (
    A2XClient,
    AsyncA2XClient,
    NotOwnedError,
    Reservation,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_client(handler, tmp_path) -> tuple[A2XClient, list[httpx.Request]]:
    recorded: list[httpx.Request] = []

    def wrapper(req: httpx.Request) -> httpx.Response:
        recorded.append(req)
        return handler(req)

    client = A2XClient(
        base_url="http://test",
        ownership_file=tmp_path / "owned.json",
    )
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(wrapper)
    )
    return client, recorded


async def _make_async(handler, tmp_path) -> tuple[AsyncA2XClient, list[httpx.Request]]:
    recorded: list[httpx.Request] = []

    async def wrapper(req: httpx.Request) -> httpx.Response:
        recorded.append(req)
        return handler(req)

    client = AsyncA2XClient(
        base_url="http://test",
        ownership_file=tmp_path / "owned.json",
    )
    await client._transport._client.aclose()
    client._transport._client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(wrapper)
    )
    return client, recorded


# ── reserve_blank_agents ─────────────────────────────────────────────────────

class TestReserveBlankAgents:
    def test_request_shape_defaults(self, tmp_path):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            captured["path"] = req.url.path
            return httpx.Response(200, json={
                "holder_id": "holder_abc",
                "ttl_seconds": 30,
                "expires_at_unix": 1234.5,
                "reservations": [{"id": "agent_1", "type": "a2a", "name": "n",
                                  "description": "__BLANK__.", "metadata": {}}],
            })

        client, _ = _make_client(handler, tmp_path)
        r = client.reserve_blank_agents("ds")
        # URL is the reservations endpoint
        assert "/reservations" in captured["path"]
        # Default filter: description=__BLANK__ AND status=online
        assert captured["body"]["filters"] == {
            "description": "__BLANK__", "status": "online",
        }
        assert captured["body"]["n"] == 1
        assert captured["body"]["ttl_seconds"] == 30
        # holder_id NOT sent when caller doesn't supply
        assert "holder_id" not in captured["body"]
        # Returned object
        assert isinstance(r, Reservation)
        assert r.holder_id == "holder_abc"
        assert r.ttl_seconds == 30
        assert len(r.agents) == 1
        client.close()

    def test_explicit_holder_id_forwarded(self, tmp_path):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={
                "holder_id": "my_holder",
                "ttl_seconds": 30, "expires_at_unix": 0,
                "reservations": [],
            })

        client, _ = _make_client(handler, tmp_path)
        client.reserve_blank_agents("ds", holder_id="my_holder")
        assert captured["body"]["holder_id"] == "my_holder"
        client.close()

    def test_extra_filters_merged(self, tmp_path):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={
                "holder_id": "x", "ttl_seconds": 30, "expires_at_unix": 0,
                "reservations": [],
            })

        client, _ = _make_client(handler, tmp_path)
        client.reserve_blank_agents("ds", extra_filters={"region": "us-east"})
        assert captured["body"]["filters"]["region"] == "us-east"
        # Defaults still present
        assert captured["body"]["filters"]["description"] == "__BLANK__"
        assert captured["body"]["filters"]["status"] == "online"
        client.close()

    def test_invalid_n_raises_no_http(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200, json={})

        client, _ = _make_client(handler, tmp_path)
        with pytest.raises(ValueError):
            client.reserve_blank_agents("ds", n=-1)
        with pytest.raises(ValueError):
            client.reserve_blank_agents("ds", ttl_seconds=0)
        assert len(sent) == 0
        client.close()


# ── release_reservation ──────────────────────────────────────────────────────

class TestReleaseReservation:
    def test_bulk_release_uses_holder_path(self, tmp_path):
        captured = {}

        def handler(req):
            if req.method == "POST":
                return httpx.Response(200, json={
                    "holder_id": "h1", "ttl_seconds": 30,
                    "expires_at_unix": 0, "reservations": [],
                })
            captured["method"] = req.method
            captured["path"] = req.url.path
            return httpx.Response(200, json={"released": ["a", "b"]})

        client, _ = _make_client(handler, tmp_path)
        r = client.reserve_blank_agents("ds")
        released = client.release_reservation(r)
        assert captured["method"] == "DELETE"
        assert captured["path"].endswith("/reservations/h1")
        assert released == ["a", "b"]
        # Marked as released — context exit becomes no-op
        assert r._released is True
        client.close()

    def test_per_sid_release_uses_sid_path(self, tmp_path):
        seen_paths = []

        def handler(req):
            if req.method == "POST":
                return httpx.Response(200, json={
                    "holder_id": "h1", "ttl_seconds": 30,
                    "expires_at_unix": 0, "reservations": [],
                })
            seen_paths.append(req.url.path)
            sid = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"released": [sid]})

        client, _ = _make_client(handler, tmp_path)
        r = client.reserve_blank_agents("ds")
        released = client.release_reservation(r, service_ids=["a", "b"])
        assert all(p.endswith("/reservations/h1/a") or p.endswith("/reservations/h1/b")
                   for p in seen_paths)
        assert sorted(released) == ["a", "b"]
        client.close()


# ── extend_reservation ───────────────────────────────────────────────────────

class TestExtendReservation:
    def test_extend_updates_local_expiry(self, tmp_path):
        def handler(req):
            if req.method == "POST" and "/extend" in req.url.path:
                return httpx.Response(200, json={"expires_at_unix": 9999.0})
            return httpx.Response(200, json={
                "holder_id": "h1", "ttl_seconds": 30,
                "expires_at_unix": 100.0, "reservations": [],
            })

        client, _ = _make_client(handler, tmp_path)
        r = client.reserve_blank_agents("ds")
        new_exp = client.extend_reservation(r, ttl_seconds=60)
        assert new_exp == 9999.0
        assert r.expires_at_unix == 9999.0
        assert r.ttl_seconds == 60
        client.close()


# ── release_my_lease ─────────────────────────────────────────────────────────

class TestReleaseMyLease:
    def test_happy_path_returns_true_on_release(self, tmp_path):
        captured = {}

        def handler(req):
            captured["path"] = req.url.path
            captured["method"] = req.method
            return httpx.Response(200, json={
                "released": True, "prev_holder_id": "leader_x",
            })

        client, _ = _make_client(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        result = client.release_my_lease("ds", "agent_1")
        assert result is True
        assert captured["method"] == "DELETE"
        assert captured["path"].endswith("/services/agent_1/lease")
        client.close()

    def test_no_lease_returns_false_no_error(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json={
                "released": False, "prev_holder_id": None,
            })

        client, _ = _make_client(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        result = client.release_my_lease("ds", "agent_1")
        assert result is False
        client.close()

    def test_foreign_sid_raises_NotOwnedError_no_http(self, tmp_path):
        sent = []

        def handler(req):
            sent.append(req)
            return httpx.Response(200, json={})

        client, _ = _make_client(handler, tmp_path)
        # NOT in _owned → NotOwnedError before any HTTP
        with pytest.raises(NotOwnedError):
            client.release_my_lease("ds", "foreign_sid")
        assert len(sent) == 0
        client.close()


# ── Auto-hook in replace_agent_card ──────────────────────────────────────────

class TestReplaceCardAutoHook:
    def test_auto_releases_lease_after_successful_replace(self, tmp_path):
        seen = {"posts": 0, "deletes": 0}

        def handler(req):
            if req.method == "POST" and "/services/a2a" in req.url.path:
                seen["posts"] += 1
                return httpx.Response(200, json={
                    "service_id": "agent_1", "dataset": "ds",
                    "status": "updated",
                })
            if req.method == "DELETE" and "/lease" in req.url.path:
                seen["deletes"] += 1
                return httpx.Response(200, json={
                    "released": True, "prev_holder_id": "leader_x",
                })
            return httpx.Response(404)

        client, _ = _make_client(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        client._blank_endpoints[("ds", "agent_1")] = "http://a"
        client.replace_agent_card("ds", "agent_1",
                                  {"name": "n", "description": "d",
                                   "endpoint": "http://a"})
        assert seen["posts"] == 1
        assert seen["deletes"] == 1  # auto-hook fired
        client.close()

    def test_release_lease_false_skips_auto_hook(self, tmp_path):
        seen = {"deletes": 0}

        def handler(req):
            if req.method == "DELETE":
                seen["deletes"] += 1
            return httpx.Response(200, json={
                "service_id": "agent_1", "dataset": "ds", "status": "updated",
            })

        client, _ = _make_client(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        client._blank_endpoints[("ds", "agent_1")] = "http://a"
        client.replace_agent_card("ds", "agent_1",
                                  {"name": "n", "description": "d",
                                   "endpoint": "http://a"},
                                  release_lease=False)
        assert seen["deletes"] == 0
        client.close()

    def test_auto_hook_swallows_404_with_warning(self, tmp_path):
        """Old backend without the lease route → 404 → warned, not raised."""
        def handler(req):
            if req.method == "POST":
                return httpx.Response(200, json={
                    "service_id": "agent_1", "dataset": "ds", "status": "updated",
                })
            # DELETE on lease route returns 404 (route not configured)
            return httpx.Response(404, json={"detail": "not found"})

        client, _ = _make_client(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        client._blank_endpoints[("ds", "agent_1")] = "http://a"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Should NOT raise, primary operation succeeds
            result = client.replace_agent_card(
                "ds", "agent_1",
                {"name": "n", "description": "d", "endpoint": "http://a"},
            )
        assert result.service_id == "agent_1"
        # Warning emitted
        assert any("release_my_lease" in str(w.message) for w in caught)
        client.close()


# ── Reservation context manager ──────────────────────────────────────────────

class TestReservationContextManager:
    def test_with_block_auto_releases_on_exit(self, tmp_path):
        seen = {"posts": 0, "deletes": 0}

        def handler(req):
            if req.method == "POST" and "/reservations" in req.url.path:
                seen["posts"] += 1
                return httpx.Response(200, json={
                    "holder_id": "h1", "ttl_seconds": 30,
                    "expires_at_unix": 0, "reservations": [],
                })
            if req.method == "DELETE":
                seen["deletes"] += 1
                return httpx.Response(200, json={"released": []})
            return httpx.Response(404)

        client, _ = _make_client(handler, tmp_path)
        with client.reserve_blank_agents("ds"):
            pass  # exit triggers release
        assert seen["posts"] == 1
        assert seen["deletes"] == 1
        client.close()

    def test_with_block_release_idempotent_after_explicit_release(self, tmp_path):
        seen = {"deletes": 0}

        def handler(req):
            if req.method == "POST":
                return httpx.Response(200, json={
                    "holder_id": "h1", "ttl_seconds": 30,
                    "expires_at_unix": 0, "reservations": [],
                })
            seen["deletes"] += 1
            return httpx.Response(200, json={"released": []})

        client, _ = _make_client(handler, tmp_path)
        with client.reserve_blank_agents("ds") as r:
            client.release_reservation(r)
        # Only ONE delete — context exit was a no-op (already released)
        assert seen["deletes"] == 1
        client.close()


# ── Async parity ─────────────────────────────────────────────────────────────

class TestAsyncReservation:
    async def test_async_reserve_returns_reservation(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json={
                "holder_id": "h_async", "ttl_seconds": 30,
                "expires_at_unix": 12345.0,
                "reservations": [{"id": "a", "type": "a2a", "name": "n",
                                  "description": "d.", "metadata": {}}],
            })

        client, _ = await _make_async(handler, tmp_path)
        r = await client.reserve_blank_agents("ds")
        assert r.holder_id == "h_async"
        assert len(r.agents) == 1
        await client.aclose()

    async def test_async_release_my_lease(self, tmp_path):
        def handler(req):
            return httpx.Response(200, json={
                "released": True, "prev_holder_id": "leader_x",
            })

        client, _ = await _make_async(handler, tmp_path)
        client._owned.add("ds", "agent_1")
        result = await client.release_my_lease("ds", "agent_1")
        assert result is True
        await client.aclose()

    async def test_async_with_block_auto_releases(self, tmp_path):
        seen = {"deletes": 0}

        def handler(req):
            if req.method == "POST":
                return httpx.Response(200, json={
                    "holder_id": "h1", "ttl_seconds": 30,
                    "expires_at_unix": 0, "reservations": [],
                })
            seen["deletes"] += 1
            return httpx.Response(200, json={"released": []})

        client, _ = await _make_async(handler, tmp_path)
        async with await client.reserve_blank_agents("ds"):
            pass
        assert seen["deletes"] == 1
        await client.aclose()
