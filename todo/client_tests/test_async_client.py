"""Async counterparts of key ``test_client.py`` cases.

Coverage goal: prove the async path honors the same ownership / cleanup rules
as the sync one, not to re-test every method.
"""

from __future__ import annotations

import httpx
import pytest

from src.client import AsyncA2XClient, NotFoundError, NotOwnedError, ValidationError

pytestmark = pytest.mark.asyncio


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
