"""Async counterparts of key ``test_client.py`` cases.

Coverage goal: prove the async path honors the same ownership / cleanup rules
as the sync one, not to re-test every method.
"""

from __future__ import annotations

import httpx
import pytest

from src.client import AsyncA2XClient, NotFoundError, NotOwnedError, ValidationError

# pytest.ini sets ``asyncio_mode = auto`` so async defs are collected as asyncio
# tests automatically; no module-level ``pytestmark`` needed.


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


async def test_async_register_persistent_false_skips_ownership(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })
    client, _ = await _make_client(handler, tmp_path)
    await client.register_agent("ds", {"name": "n", "description": "d"}, persistent=False)
    assert not client._owned.contains("ds", "sid")
    await client.aclose()


async def test_async_404_clears_ownership(tmp_path):
    def handler(req):
        return httpx.Response(404, json={"detail": "not found"})
    client, _ = await _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(NotFoundError):
        await client.update_agent("ds", "sid", {"description": "x"})
    assert not client._owned.contains("ds", "sid")
    await client.aclose()


async def test_async_delete_dataset_400_still_clears(tmp_path):
    def handler(req):
        return httpx.Response(400, json={"detail": "does not exist"})
    client, _ = await _make_client(handler, tmp_path)
    client._owned.add("ds", "a")
    with pytest.raises(ValidationError):
        await client.delete_dataset("ds")
    assert not client._owned.contains("ds", "a")
    await client.aclose()


async def test_async_ownership_guard_no_http(tmp_path):
    def handler(req):
        pytest.fail("HTTP should not be called")
        raise AssertionError
    client, reqs = await _make_client(handler, tmp_path)
    with pytest.raises(NotOwnedError):
        await client.update_agent("ds", "nope", {"description": "x"})
    assert reqs == []
    await client.aclose()


async def test_async_base_url_is_read_only(tmp_path):
    client = AsyncA2XClient(base_url="http://h/", ownership_file=tmp_path / "x.json")
    with pytest.raises(AttributeError):
        client.base_url = "http://other/"  # type: ignore[misc]
    await client.aclose()


# ── Symmetry with sync client (A-PAR-*) ─────────────────────────────────────

class TestSyncAsyncSymmetry:
    def test_method_set_matches(self):
        import inspect
        from src.client import A2XClient

        business = {"create_dataset", "delete_dataset", "register_agent",
                    "update_agent", "set_status", "list_agents",
                    "get_agent", "deregister_agent"}
        sync_names = {m for m, _ in inspect.getmembers(A2XClient, inspect.isfunction)}
        async_names = {m for m, _ in inspect.getmembers(AsyncA2XClient, inspect.isfunction)}
        assert business <= sync_names
        assert business <= async_names

    @pytest.mark.parametrize("method_name", [
        "create_dataset", "delete_dataset", "register_agent",
        "update_agent", "set_status", "list_agents",
        "get_agent", "deregister_agent",
    ])
    def test_method_signatures_match(self, method_name):
        import inspect
        from src.client import A2XClient
        sync_sig = inspect.signature(getattr(A2XClient, method_name))
        async_sig = inspect.signature(getattr(AsyncA2XClient, method_name))
        assert list(sync_sig.parameters) == list(async_sig.parameters)

    @pytest.mark.parametrize("method_name", [
        "create_dataset", "delete_dataset", "register_agent",
        "update_agent", "set_status", "list_agents",
        "get_agent", "deregister_agent",
    ])
    def test_async_methods_are_coroutine_functions(self, method_name):
        import inspect
        assert inspect.iscoroutinefunction(getattr(AsyncA2XClient, method_name))


# ── Lifecycle (A-LC-*) ──────────────────────────────────────────────────────

async def test_async_context_manager_closes(tmp_path):
    async with AsyncA2XClient(base_url="http://test",
                              ownership_file=tmp_path / "x.json") as client:
        inner = client._transport._client
        assert inner.is_closed is False
    assert inner.is_closed is True


async def test_aclose_is_idempotent(tmp_path):
    client = AsyncA2XClient(base_url="http://test", ownership_file=tmp_path / "x.json")
    await client.aclose()
    await client.aclose()  # second close must not raise


async def test_async_base_url_normalized(tmp_path):
    client = AsyncA2XClient(base_url="http://host", ownership_file=tmp_path / "x.json")
    assert client.base_url == "http://host/"
    await client.aclose()


# ── Concurrency (A-CC-*) ────────────────────────────────────────────────────

async def test_async_gather_register_many(tmp_path):
    """Concurrent register_agent via gather — all results tracked in _owned."""
    import asyncio

    recorded = []

    def handler(req):
        recorded.append(req)
        sid = f"sid_{len(recorded)}"
        return httpx.Response(200, json={
            "service_id": sid, "dataset": "ds", "status": "registered",
        })

    client, _ = await _make_client(handler, tmp_path)
    cards = [{"name": f"N{i}", "description": "D"} for i in range(10)]
    results = await asyncio.gather(*[
        client.register_agent("ds", c) for c in cards
    ])
    assert len(results) == 10
    owned = set(client._owned._data.get("ds", set()))
    assert owned == {f"sid_{i}" for i in range(1, 11)}
    await client.aclose()


async def test_async_gather_mixed_success_and_failure(tmp_path):
    """return_exceptions: only successful registrations land in _owned."""
    import asyncio

    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        n = state["n"]
        if n == 2:
            return httpx.Response(400, json={"detail": "bad"})
        return httpx.Response(200, json={
            "service_id": f"sid_{n}", "dataset": "ds", "status": "registered",
        })

    client, _ = await _make_client(handler, tmp_path)
    results = await asyncio.gather(*[
        client.register_agent("ds", {"name": f"N{i}", "description": "D"})
        for i in range(3)
    ], return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], ValidationError)
    assert len(successes) == 2

    owned = client._owned._data.get("ds", set())
    assert len(owned) == 2
    await client.aclose()


# ── Error propagation (A-ERR-*) ─────────────────────────────────────────────

async def test_server_error_raises_from_await(tmp_path):
    from src.client import ServerError

    def handler(req):
        return httpx.Response(500, json={"detail": "internal"})

    client, _ = await _make_client(handler, tmp_path)
    with pytest.raises(ServerError):
        await client.create_dataset("ds")
    await client.aclose()


async def test_async_connection_error_mapped(tmp_path):
    from src.client import A2XConnectionError

    def handler(req):
        raise httpx.ConnectError("refused")

    client, _ = await _make_client(handler, tmp_path)
    with pytest.raises(A2XConnectionError):
        await client.list_agents("ds")
    await client.aclose()


# ── Shared OwnershipStore between sync and async clients (A-SH-*) ──────────

async def test_sync_registered_readable_by_async(tmp_path):
    """Sync client writes ownership; a later async client on the same file sees it."""
    from src.client import A2XClient

    ownership = tmp_path / "shared.json"

    def sync_handler(req):
        return httpx.Response(200, json={
            "service_id": "shared_sid", "dataset": "ds", "status": "registered",
        })

    sync_client = A2XClient(base_url="http://shared", ownership_file=ownership)
    sync_client._transport._client.close()
    sync_client._transport._client = httpx.Client(
        base_url=sync_client.base_url,
        transport=httpx.MockTransport(sync_handler),
    )
    sync_client.register_agent("ds", {"name": "N", "description": "D"})
    sync_client.close()

    # A fresh async client on the same file + base_url must inherit ownership.
    fresh = AsyncA2XClient(base_url="http://shared", ownership_file=ownership)
    assert fresh._owned.contains("ds", "shared_sid")
    await fresh.aclose()
