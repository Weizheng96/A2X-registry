"""End-to-end tests for ``A2XClient`` using ``httpx.MockTransport``.

Exercises the full code path: body construction → HTTP → ownership updates.
"""

from __future__ import annotations

import httpx
import pytest

from src.client import (
    A2XClient,
    NotFoundError,
    NotOwnedError,
    UnexpectedServiceTypeError,
    ValidationError,
)


def _make_client(handler, tmp_path) -> tuple[A2XClient, list[httpx.Request]]:
    """Construct a client whose transport is backed by an httpx MockTransport."""
    recorded: list[httpx.Request] = []

    def wrapper(req: httpx.Request) -> httpx.Response:
        recorded.append(req)
        return handler(req)

    client = A2XClient(
        base_url="http://test",
        ownership_file=tmp_path / "owned.json",
    )
    # Replace the inner httpx client after __init__ so default path works.
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(wrapper)
    )
    return client, recorded


# ── create_dataset ───────────────────────────────────────────────────────────

def test_create_dataset_defaults_to_a2a_only(tmp_path):
    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        assert body["formats"] == {"a2a": "v0.0"}
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })
    client, reqs = _make_client(handler, tmp_path)
    r = client.create_dataset("ds", embedding_model="m")
    assert r.status == "created"
    assert reqs[0].method == "POST"
    client.close()


def test_create_dataset_explicit_none_omits_formats(tmp_path):
    def handler(req):
        assert b"formats" not in req.content
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m", "formats": {}, "status": "created",
        })
    client, _ = _make_client(handler, tmp_path)
    client.create_dataset("ds", embedding_model="m", formats=None)
    client.close()


# ── register_agent ───────────────────────────────────────────────────────────

def test_register_agent_persistent_true_records_ownership(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })
    client, _ = _make_client(handler, tmp_path)
    r = client.register_agent("ds", {"protocolVersion": "0.0", "name": "n", "description": "d"})
    assert r.service_id == "sid"
    assert client._owned.contains("ds", "sid")
    client.close()


def test_register_agent_persistent_false_skips_ownership(tmp_path):
    """D4: ephemeral entries would 404 after backend restart if tracked locally."""
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })
    client, _ = _make_client(handler, tmp_path)
    client.register_agent("ds", {"name": "n", "description": "d"}, persistent=False)
    assert not client._owned.contains("ds", "sid")
    client.close()


# ── ownership guard ─────────────────────────────────────────────────────────

def test_update_without_ownership_fails_without_http(tmp_path):
    def handler(req):
        pytest.fail("HTTP should not be called on ownership failure")
        raise AssertionError
    client, reqs = _make_client(handler, tmp_path)
    with pytest.raises(NotOwnedError):
        client.update_agent("ds", "never-registered", {"description": "x"})
    assert reqs == []
    client.close()


@pytest.mark.parametrize("op", ["update", "set_count", "deregister"])
def test_404_clears_ownership(tmp_path, op):
    """D3 regression: 404 on any mutation auto-cleans local _owned."""
    def handler(req):
        return httpx.Response(404, json={"detail": "Service not found"})
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")

    with pytest.raises(NotFoundError):
        if op == "update":
            client.update_agent("ds", "sid", {"description": "x"})
        elif op == "set_count":
            client.set_team_count("ds", "sid", 0)
        else:
            client.deregister_agent("ds", "sid")
    assert not client._owned.contains("ds", "sid")
    client.close()


# ── delete_dataset ──────────────────────────────────────────────────────────

def test_delete_dataset_success_clears_ownership(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"dataset": "ds", "status": "deleted"})
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "a")
    client._owned.add("ds", "b")
    client.delete_dataset("ds")
    assert not client._owned.contains("ds", "a")
    assert not client._owned.contains("ds", "b")
    client.close()


def test_delete_dataset_400_still_clears_ownership(tmp_path):
    """D6: backend 400 (dataset already gone) should still clean local _owned."""
    def handler(req):
        return httpx.Response(400, json={"detail": "Dataset 'ds' does not exist"})
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "a")
    with pytest.raises(ValidationError):
        client.delete_dataset("ds")
    assert not client._owned.contains("ds", "a")
    client.close()


# ── set_team_count ──────────────────────────────────────────────────────────

def test_set_team_count_sends_correct_field(tmp_path):
    def handler(req):
        import json as _json
        assert _json.loads(req.content) == {"agentTeamCount": 7}
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["agentTeamCount"], "taxonomy_affected": False,
        })
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    client.set_team_count("ds", "sid", 7)
    client.close()


def test_set_team_count_rejects_negative_before_http(tmp_path):
    """D10: ValueError fires locally, no HTTP call issued."""
    def handler(req):
        pytest.fail("HTTP should not be called")
        raise AssertionError
    client, reqs = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(ValueError):
        client.set_team_count("ds", "sid", -1)
    assert reqs == []
    client.close()


# ── get_agent / list_agents ─────────────────────────────────────────────────

def test_list_agents_parses_brief_array(tmp_path):
    def handler(req):
        assert "mode=browse" in str(req.url)
        return httpx.Response(200, json=[
            {"id": "a", "name": "A", "description": "a-desc"},
            {"id": "b", "name": "B", "description": "b-desc"},
        ])
    client, _ = _make_client(handler, tmp_path)
    agents = client.list_agents("ds")
    assert [a.id for a in agents] == ["a", "b"]
    client.close()


def test_get_agent_parses_metadata_and_raw(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "id": "sid", "type": "a2a", "name": "N", "description": "D",
            "metadata": {"protocolVersion": "0.0", "name": "N", "agentTeamCount": 3},
        })
    client, _ = _make_client(handler, tmp_path)
    detail = client.get_agent("ds", "sid")
    assert detail.id == "sid"
    assert detail.metadata["agentTeamCount"] == 3
    # raw preserves entire response for fields not in the dataclass
    assert detail.raw["type"] == "a2a"
    client.close()


def test_get_agent_with_zip_content_type_raises(tmp_path):
    """D5: content-type with parameters still triggers the ZIP branch."""
    def handler(req):
        return httpx.Response(
            200,
            content=b"\x00\x00",
            headers={"content-type": "application/zip; charset=binary"},
        )
    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(UnexpectedServiceTypeError):
        client.get_agent("ds", "sid")
    client.close()


def test_get_agent_with_charset_json_parses_ok(tmp_path):
    """D5: application/json; charset=utf-8 should be accepted."""
    def handler(req):
        return httpx.Response(
            200,
            json={"id": "sid", "type": "a2a", "name": "N", "description": "D", "metadata": {}},
            headers={"content-type": "application/json; charset=utf-8"},
        )
    client, _ = _make_client(handler, tmp_path)
    detail = client.get_agent("ds", "sid")
    assert detail.name == "N"
    client.close()


# ── base_url / attribute immutability (L2 / L3) ─────────────────────────────

def test_base_url_auto_normalizes(tmp_path):
    client = A2XClient(base_url="http://host", ownership_file=tmp_path / "x.json")
    assert client.base_url == "http://host/"
    client.close()


def test_base_url_is_read_only(tmp_path):
    client = A2XClient(base_url="http://host/", ownership_file=tmp_path / "x.json")
    with pytest.raises(AttributeError):
        client.base_url = "http://other/"  # type: ignore[misc]
    client.close()


def test_subpath_base_url_preserved_in_request(tmp_path):
    """L3: request under ``http://h/prefix/`` must hit ``/prefix/api/datasets``."""
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })

    client = A2XClient(base_url="http://h/prefix/", ownership_file=tmp_path / "x.json")
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    client.create_dataset("ds", embedding_model="m")
    assert seen["path"] == "/prefix/api/datasets"
    client.close()
