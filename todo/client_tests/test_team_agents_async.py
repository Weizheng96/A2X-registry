"""Async mirror of key team-agent tests.

Goal: verify the async path honors identical semantics — not to re-test
every case covered by ``test_team_agents.py``. Focus on paths that
involve ``asyncio.to_thread`` (ownership writes + blank-endpoint cache).
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.client import AsyncA2XClient, NotFoundError, NotOwnedError


async def _make_client(handler, tmp_path) -> tuple[AsyncA2XClient, list[httpx.Request]]:
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


def _mk_wrapped(sid: str, card: dict) -> dict:
    return {
        "id": sid, "type": "a2a",
        "name": card["name"], "description": card["description"] + ".",
        "metadata": card,
    }


# ── register_blank_agent ──────────────────────────────────────────────────────

async def test_async_register_blank_populates_ownership_and_cache(tmp_path):
    def handler(req):
        body = json.loads(req.content)
        assert body["agent_card"]["description"] == "__BLANK__"
        return httpx.Response(200, json={
            "service_id": "agent_x", "dataset": "t", "status": "registered",
        })

    client, _ = await _make_client(handler, tmp_path)
    await client.register_blank_agent("t", endpoint="http://a", service_id="agent_x")
    assert client._owned.contains("t", "agent_x")
    assert client._blank_endpoints[("t", "agent_x")] == "http://a"
    await client.aclose()


async def test_async_register_blank_rejects_empty_endpoint(tmp_path):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200)

    client, _ = await _make_client(handler, tmp_path)
    with pytest.raises(ValueError):
        await client.register_blank_agent("t", endpoint="")
    assert len(sent) == 0
    await client.aclose()


# ── list_agents / list_idle_blank_agents ────────────────────────────────────

async def test_async_list_agents_flat_shape(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            _mk_wrapped("a", {"name": "nA", "description": "__BLANK__",
                              "endpoint": "http://a", "agentTeamCount": 0}),
        ])

    client, _ = await _make_client(handler, tmp_path)
    agents = await client.list_agents("t", description="__BLANK__")
    assert agents[0]["id"] == "a"
    assert agents[0]["endpoint"] == "http://a"
    assert agents[0]["description"] == "__BLANK__"
    await client.aclose()


async def test_async_list_idle_sorted_ascending(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[
            _mk_wrapped("a", {"name": "nA", "description": "__BLANK__",
                              "endpoint": "http://a", "agentTeamCount": 3}),
            _mk_wrapped("b", {"name": "nB", "description": "__BLANK__",
                              "endpoint": "http://b", "agentTeamCount": 0}),
        ])

    client, _ = await _make_client(handler, tmp_path)
    idle = await client.list_idle_blank_agents("t", n=5)
    assert [a["id"] for a in idle] == ["b", "a"]
    await client.aclose()


async def test_async_list_idle_n_zero_no_http(tmp_path):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200)

    client, _ = await _make_client(handler, tmp_path)
    assert await client.list_idle_blank_agents("t", n=0) == []
    assert len(sent) == 0
    await client.aclose()


# ── replace_agent_card / restore_to_blank ────────────────────────────────────

async def test_async_replace_foreign_sid_raises_NotOwnedError_first(tmp_path):
    """Ownership now precedes card validation — even card without endpoint."""
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200)

    client, _ = await _make_client(handler, tmp_path)
    with pytest.raises(NotOwnedError):
        await client.replace_agent_card(
            "t", "foreign", {"name": "n", "description": "d"}
        )
    assert len(sent) == 0
    await client.aclose()


async def test_async_replace_not_owned_no_http(tmp_path):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200)

    client, _ = await _make_client(handler, tmp_path)
    with pytest.raises(NotOwnedError):
        await client.replace_agent_card(
            "t", "foreign",
            {"name": "n", "description": "d", "endpoint": "http://e"},
        )
    assert len(sent) == 0
    await client.aclose()


async def test_async_replace_404_clears_caches(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={
                "service_id": "x", "dataset": "t", "status": "registered",
            })
        return httpx.Response(404, json={"detail": "not found"})

    client, _ = await _make_client(handler, tmp_path)
    await client.register_blank_agent("t", endpoint="http://a", service_id="x")
    with pytest.raises(NotFoundError):
        await client.replace_agent_card(
            "t", "x", {"name": "n", "description": "d", "endpoint": "http://a"}
        )
    assert not client._owned.contains("t", "x")
    assert ("t", "x") not in client._blank_endpoints
    await client.aclose()


async def test_async_restore_L1_hit_single_post(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "x", "dataset": "t", "status": "registered",
        })

    client, recorded = await _make_client(handler, tmp_path)
    await client.register_blank_agent("t", endpoint="http://a", service_id="x")
    before = len(recorded)
    await client.restore_to_blank("t", "x")
    # Exactly 1 POST (no GET for L2)
    assert len(recorded) - before == 1
    assert recorded[-1].method == "POST"
    await client.aclose()


async def test_async_restore_L2_reads_endpoint_from_card(tmp_path):
    card = {
        "name": "_BlankAgent_http://a",
        "description": "__BLANK__",
        "endpoint": "http://a",
        "agentTeamCount": 0,
    }

    def handler(req):
        p = req.url.path
        params = dict(req.url.params)
        if req.method == "POST" and "services/a2a" in p:
            return httpx.Response(200, json={
                "service_id": "x", "dataset": "t", "status": "registered",
            })
        if req.method == "GET" and p.endswith("/services") and params.get("mode") == "single":
            return httpx.Response(200, json={
                "id": "x", "type": "a2a", "name": card["name"],
                "description": card["description"] + ".", "metadata": card,
            })
        return httpx.Response(404)

    client, recorded = await _make_client(handler, tmp_path)
    await client.register_blank_agent("t", endpoint="http://a", service_id="x")
    client._blank_endpoints.clear()  # force L2
    before = len(recorded)
    await client.restore_to_blank("t", "x")
    # GET (single) + POST (replace) = 2 calls
    assert len(recorded) - before == 2
    await client.aclose()


async def test_async_restore_L3_no_endpoint_in_card_raises(tmp_path):
    def handler(req):
        p = req.url.path
        params = dict(req.url.params)
        if req.method == "POST" and "services/a2a" in p:
            return httpx.Response(200, json={
                "service_id": "x", "dataset": "t", "status": "registered",
            })
        if req.method == "GET" and p.endswith("/services") and params.get("mode") == "single":
            return httpx.Response(200, json={
                "id": "x", "type": "a2a", "name": "broken",
                "description": "d.", "metadata": {"name": "broken", "description": "d"},
            })  # endpoint missing
        return httpx.Response(404)

    client, _ = await _make_client(handler, tmp_path)
    await client.register_blank_agent("t", endpoint="http://a", service_id="x")
    client._blank_endpoints.clear()
    with pytest.raises(ValueError, match=r"No 'endpoint' available"):
        await client.restore_to_blank("t", "x")
    await client.aclose()
