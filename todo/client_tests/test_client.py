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


@pytest.mark.parametrize("op", ["update", "set_status", "deregister"])
def test_404_clears_ownership(tmp_path, op):
    """D3 regression: 404 on any mutation auto-cleans local _owned."""
    def handler(req):
        return httpx.Response(404, json={"detail": "Service not found"})
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")

    with pytest.raises(NotFoundError):
        if op == "update":
            client.update_agent("ds", "sid", {"description": "x"})
        elif op == "set_status":
            client.set_status("ds", "sid", "online")
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


# ── set_status ──────────────────────────────────────────────────────────────

def test_set_status_sends_correct_field(tmp_path):
    def handler(req):
        import json as _json
        assert _json.loads(req.content) == {"status": "busy"}
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["status"], "taxonomy_affected": False,
        })
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    client.set_status("ds", "sid", "busy")
    client.close()


def test_set_status_rejects_invalid_enum_before_http(tmp_path):
    """D10: ValueError fires locally for invalid status, no HTTP call issued."""
    def handler(req):
        pytest.fail("HTTP should not be called")
        raise AssertionError
    client, reqs = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(ValueError, match=r"status must be one of"):
        client.set_status("ds", "sid", "invalid_status")
    assert reqs == []
    client.close()


# ── get_agent / list_agents ─────────────────────────────────────────────────

def test_list_agents_no_filters_hits_mode_filter_with_no_params(tmp_path):
    """list_agents(ds) should send mode=filter alone → backend returns all."""
    captured = {}

    def handler(req):
        captured["params"] = dict(req.url.params.multi_items())
        return httpx.Response(200, json=[
            {"id": "a", "type": "a2a", "name": "A", "description": "A.",
             "metadata": {"name": "A", "description": "A", "endpoint": "http://a"}},
            {"id": "b", "type": "a2a", "name": "B", "description": "B.",
             "metadata": {"name": "B", "description": "B", "endpoint": "http://b"}},
        ])
    client, _ = _make_client(handler, tmp_path)
    agents = client.list_agents("ds")
    assert captured["params"] == {"mode": "filter"}
    assert [a["id"] for a in agents] == ["a", "b"]
    # Flat shape: metadata keys surface at top level; a2a raw description
    # overrides build_description (the "A." with trailing period)
    assert agents[0]["description"] == "A"
    assert agents[0]["endpoint"] == "http://a"
    client.close()


def test_get_agent_parses_metadata_and_raw(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "id": "sid", "type": "a2a", "name": "N", "description": "D",
            "metadata": {"protocolVersion": "0.0", "name": "N", "status": "busy"},
        })
    client, _ = _make_client(handler, tmp_path)
    detail = client.get_agent("ds", "sid")
    assert detail.id == "sid"
    assert detail.metadata["status"] == "busy"
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


# ── create_dataset additional cases ──────────────────────────────────────────

def test_create_dataset_custom_embedding_model(tmp_path):
    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        assert body["embedding_model"] == "bge-m3"
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "bge-m3",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })
    client, _ = _make_client(handler, tmp_path)
    client.create_dataset("ds", embedding_model="bge-m3")
    client.close()


def test_create_dataset_custom_formats(tmp_path):
    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        assert body["formats"] == {"a2a": "v0.0", "generic": "v0.0"}
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m",
            "formats": body["formats"], "status": "created",
        })
    client, _ = _make_client(handler, tmp_path)
    client.create_dataset("ds", embedding_model="m",
                          formats={"a2a": "v0.0", "generic": "v0.0"})
    client.close()


def test_create_dataset_does_not_touch_ownership(tmp_path):
    """Datasets have no per-client creator tracking."""
    def handler(req):
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })
    client, _ = _make_client(handler, tmp_path)
    client.create_dataset("ds")
    assert client._owned._data == {}
    client.close()


def test_create_dataset_validation_error(tmp_path):
    def handler(req):
        return httpx.Response(400, json={"detail": "invalid name"})
    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(ValidationError) as exc_info:
        client.create_dataset("bad")
    assert exc_info.value.payload["detail"] == "invalid name"
    client.close()


# ── register_agent — full-card preservation and corner cases ─────────────────

def test_register_agent_full_card_passes_through_unchanged(tmp_path):
    """Agent card fields (camelCase, nested, custom) must not be rewritten."""
    card = {
        "protocolVersion": "0.0",
        "name": "N", "description": "D",
        "skills": [{"name": "s", "description": "d"}],
        "provider": {"organization": "O", "url": "U"},
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "status": "online",
        "custom_x": 42,
    }
    captured = {}

    def handler(req):
        import json as _json
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })

    client, _ = _make_client(handler, tmp_path)
    client.register_agent("ds", card)
    # agent_card field wraps the original dict unchanged
    assert captured["body"]["agent_card"] == card
    # camelCase must not be mangled to snake_case
    assert "protocolVersion" in captured["body"]["agent_card"]
    assert "defaultInputModes" in captured["body"]["agent_card"]
    client.close()


def test_register_agent_with_explicit_service_id(tmp_path):
    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        assert body["service_id"] == "my_custom_id"
        return httpx.Response(200, json={
            "service_id": "my_custom_id", "dataset": "ds", "status": "registered",
        })
    client, _ = _make_client(handler, tmp_path)
    r = client.register_agent("ds", {"name": "N", "description": "D"},
                              service_id="my_custom_id")
    assert r.service_id == "my_custom_id"
    assert client._owned.contains("ds", "my_custom_id")
    client.close()


def test_register_agent_service_id_none_not_in_body(tmp_path):
    """service_id=None should be filtered out of the body."""
    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        assert "service_id" not in body
        return httpx.Response(200, json={
            "service_id": "auto-generated", "dataset": "ds", "status": "registered",
        })
    client, _ = _make_client(handler, tmp_path)
    client.register_agent("ds", {"name": "N", "description": "D"})
    client.close()


def test_register_agent_updated_status_also_tracked(tmp_path):
    """Re-registering same service_id returns status=updated; still add to _owned."""
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
        })
    client, _ = _make_client(handler, tmp_path)
    r = client.register_agent("ds", {"name": "N", "description": "D"})
    assert r.status == "updated"
    assert client._owned.contains("ds", "sid")
    client.close()


def test_register_agent_400_does_not_track(tmp_path):
    """Validation failures must leave _owned clean."""
    def handler(req):
        return httpx.Response(400, json={"detail": "bad format"})
    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(ValidationError):
        client.register_agent("ds", {"name": "N", "description": "D"})
    assert client._owned._data == {}
    client.close()


# ── update_agent — body + response ───────────────────────────────────────────

def test_update_agent_body_is_fields_dict_verbatim(tmp_path):
    """Body is the fields dict itself, no wrapping."""
    captured = {}

    def handler(req):
        import json as _json
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["description", "skills"], "taxonomy_affected": False,
        })

    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    fields = {"description": "new", "skills": [{"name": "s", "description": "d"}]}
    r = client.update_agent("ds", "sid", fields)
    assert captured["body"] == fields
    assert r.changed_fields == ["description", "skills"]
    assert r.taxonomy_affected is False
    client.close()


def test_update_agent_taxonomy_affected_true(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["name"], "taxonomy_affected": True,
        })
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    r = client.update_agent("ds", "sid", {"name": "NewName"})
    assert r.taxonomy_affected is True
    client.close()


def test_update_agent_400_does_not_clear_ownership(tmp_path):
    """Validation errors (not 404) must not touch _owned."""
    def handler(req):
        return httpx.Response(400, json={"detail": "conflict"})
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(ValidationError):
        client.update_agent("ds", "sid", {"name": "X"})
    assert client._owned.contains("ds", "sid")
    client.close()


# ── set_status edge cases ────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["online", "busy", "offline"])
def test_set_status_each_valid_value(tmp_path, status):
    """Each valid enum value passes through to the backend."""
    def handler(req):
        import json as _json
        assert _json.loads(req.content) == {"status": status}
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["status"], "taxonomy_affected": False,
        })
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    client.set_status("ds", "sid", status)
    client.close()


@pytest.mark.parametrize("bad", ["ONLINE", "available", "", None, 0, True, " online "])
def test_set_status_rejects_invalid(tmp_path, bad):
    def handler(req):
        pytest.fail("HTTP should not be called on invalid status")
    client, reqs = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(ValueError):
        client.set_status("ds", "sid", bad)
    assert reqs == []
    client.close()


def test_set_status_field_name_fixed(tmp_path):
    """Field name is SDK-owned, callers cannot influence it."""
    captured = {}

    def handler(req):
        import json as _json
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "updated",
            "changed_fields": [], "taxonomy_affected": False,
        })

    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    client.set_status("ds", "sid", "busy")
    # exactly one top-level key; name fixed
    assert list(captured["body"].keys()) == ["status"]
    client.close()


# ── list_agents ──────────────────────────────────────────────────────────────

def test_list_agents_empty_returns_empty_list(tmp_path):
    def handler(req):
        return httpx.Response(200, json=[])
    client, _ = _make_client(handler, tmp_path)
    assert client.list_agents("ds") == []
    client.close()


def test_list_agents_no_ownership_required(tmp_path):
    """Reads work even when _owned is empty."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": "a", "type": "a2a", "name": "A", "description": "A.",
             "metadata": {"name": "A", "description": "A"}},
        ])
    client, _ = _make_client(handler, tmp_path)
    assert client._owned._data == {}
    agents = client.list_agents("ds")
    assert len(agents) == 1
    client.close()


def test_list_agents_with_filter_sends_query_params(tmp_path):
    captured = {}

    def handler(req):
        captured["params"] = dict(req.url.params.multi_items())
        return httpx.Response(200, json=[])

    client, _ = _make_client(handler, tmp_path)
    client.list_agents("ds", description="__BLANK__", priority=0)
    assert captured["params"]["mode"] == "filter"
    assert captured["params"]["description"] == "__BLANK__"
    # Non-string values are coerced to strings (HTTP query params)
    assert captured["params"]["priority"] == "0"
    client.close()


def test_list_agents_flat_shape_merges_metadata(tmp_path):
    """Return dicts should have metadata keys merged at top level."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": "sid", "type": "a2a", "name": "N", "description": "build_desc.",
             "metadata": {"name": "N", "description": "raw_desc",
                          "endpoint": "http://e", "status": "busy"}},
        ])
    client, _ = _make_client(handler, tmp_path)
    agents = client.list_agents("ds")
    a = agents[0]
    # wrapper fields at top level
    assert a["id"] == "sid"
    assert a["type"] == "a2a"
    # metadata.description overrides wrapper.description (raw > transformed)
    assert a["description"] == "raw_desc"
    # card fields surfaced at top level
    assert a["endpoint"] == "http://e"
    assert a["status"] == "busy"
    # no nested metadata
    assert "metadata" not in a
    client.close()


def test_list_agents_generic_shape_preserves_wrapper_name(tmp_path):
    """Generic services: metadata lacks name/description → wrapper survives."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": "g", "type": "generic", "name": "GenSvc", "description": "generic desc",
             "metadata": {"url": "http://g", "inputSchema": {"type": "object"}}},
        ])
    client, _ = _make_client(handler, tmp_path)
    agents = client.list_agents("ds")
    g = agents[0]
    assert g["name"] == "GenSvc"
    assert g["description"] == "generic desc"
    assert g["url"] == "http://g"
    assert g["inputSchema"] == {"type": "object"}
    client.close()


def test_list_agents_malformed_entries_skipped(tmp_path):
    """Non-dict items in the array are dropped defensively."""
    def handler(req):
        return httpx.Response(200, json=[
            {"id": "a", "type": "a2a", "name": "A", "description": "A",
             "metadata": {"name": "A"}},
            "not-a-dict",
            42,
            None,
        ])
    client, _ = _make_client(handler, tmp_path)
    agents = client.list_agents("ds")
    assert len(agents) == 1
    assert agents[0]["id"] == "a"
    client.close()


def test_list_agents_rejects_reserved_filter_key(tmp_path):
    """Reserved keys (mode/service_id/size/page) raise locally, no HTTP."""
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json=[])

    client, _ = _make_client(handler, tmp_path)
    for k in ["mode", "service_id", "size", "page"]:
        with pytest.raises(ValueError, match=r"collides with a reserved query param"):
            client.list_agents("ds", **{k: "x"})
    assert len(sent) == 0
    client.close()


def test_list_agents_rejects_none_filter_value(tmp_path):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json=[])

    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(ValueError, match=r"must not be None"):
        client.list_agents("ds", description=None)
    assert len(sent) == 0
    client.close()


# ── get_agent — URL and edge cases ───────────────────────────────────────────

def test_get_agent_query_params_correct(tmp_path):
    captured = {}

    def handler(req):
        captured["query"] = dict(req.url.params.multi_items())
        return httpx.Response(200, json={
            "id": "sid", "type": "a2a", "name": "N", "description": "D", "metadata": {},
        })

    client, _ = _make_client(handler, tmp_path)
    client.get_agent("ds", "sid")
    assert captured["query"]["mode"] == "single"
    assert captured["query"]["service_id"] == "sid"
    client.close()


def test_get_agent_404(tmp_path):
    def handler(req):
        return httpx.Response(404, json={"detail": "not found"})
    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(NotFoundError):
        client.get_agent("ds", "no_such_sid")
    client.close()


def test_get_agent_malformed_json_body_raises(tmp_path):
    """Unexpected: 200 but body is not a JSON object → raise, don't crash."""
    def handler(req):
        return httpx.Response(
            200, json="not a dict",
            headers={"content-type": "application/json"},
        )
    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(UnexpectedServiceTypeError):
        client.get_agent("ds", "sid")
    client.close()


# ── URL encoding in paths (X-URL-*) ─────────────────────────────────────────

@pytest.mark.parametrize("ds_name, encoded_segment", [
    ("my team", "my%20team"),
    ("研究团队", "%E7%A0%94%E7%A9%B6%E5%9B%A2%E9%98%9F"),
    ("my#ds", "my%23ds"),
])
def test_dataset_name_url_encoded_on_wire(tmp_path, ds_name, encoded_segment):
    """The encoded form must appear in the on-the-wire raw path.

    httpx's ``url.path`` is percent-decoded for display; ``url.raw_path`` is
    the bytes actually sent. We assert against the latter.
    """
    captured = {}

    def handler(req):
        captured["raw_path"] = req.url.raw_path
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": ds_name, "status": "registered",
        })

    client, _ = _make_client(handler, tmp_path)
    client.register_agent(ds_name, {"name": "N", "description": "D"})
    assert encoded_segment.encode("ascii") in captured["raw_path"]
    client.close()


def test_service_id_with_slash_stays_in_query_string(tmp_path):
    """get_agent places service_id in query string → slashes can't walk the URL."""
    captured = {}

    def handler(req):
        captured["path"] = req.url.path
        captured["raw_query"] = req.url.query.decode()
        return httpx.Response(200, json={
            "id": "s/../x", "type": "a2a", "name": "N", "description": "D", "metadata": {},
        })

    client, _ = _make_client(handler, tmp_path)
    client.get_agent("ds", "s/../x")
    # Path must stop at /services — the slash-laden sid never enters the path.
    assert captured["path"].endswith("/services")
    assert "service_id=s%2F..%2Fx" in captured["raw_query"]
    client.close()


def test_service_id_with_slash_encoded_in_path(tmp_path):
    """update_agent / deregister_agent put sid in path → must URL-encode."""
    captured = {}

    def handler(req):
        captured["raw_path"] = req.url.raw_path
        return httpx.Response(200, json={
            "service_id": "s/1", "dataset": "ds", "status": "updated",
            "changed_fields": [], "taxonomy_affected": False,
        })

    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "s/1")
    client.update_agent("ds", "s/1", {"description": "x"})
    # Encoded %2F must appear on the wire; literal slash would split segments.
    assert b"/s%2F1" in captured["raw_path"]
    client.close()


# ── api_key injection (M-INIT-005/006) ───────────────────────────────────────

def test_api_key_injected_as_bearer(tmp_path):
    captured = {}

    def handler(req):
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=[])

    client = A2XClient(
        base_url="http://test", api_key="secret_token",
        ownership_file=tmp_path / "x.json",
    )
    # Preserve the default headers from __init__ when swapping the transport.
    default_headers = dict(client._transport._client.headers)
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url,
        headers=default_headers,
        transport=httpx.MockTransport(handler),
    )
    client.list_agents("ds")
    assert captured["auth"] == "Bearer secret_token"
    client.close()


def test_no_api_key_means_no_auth_header(tmp_path):
    captured = {}

    def handler(req):
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=[])

    client, _ = _make_client(handler, tmp_path)
    client.list_agents("ds")
    assert captured["auth"] is None
    client.close()


# ── Pre-existing owned.json loaded on init (M-INIT-009) ─────────────────────

def test_preexisting_owned_json_is_loaded(tmp_path):
    """Starting a new client should pick up ownership from disk."""
    import json as _json
    path = tmp_path / "owned.json"
    path.write_text(_json.dumps({
        "schema_version": 1,
        "data": {"http://test/": {"ds": ["pre_sid"]}},
    }), encoding="utf-8")

    def handler(req):
        return httpx.Response(200, json={
            "service_id": "pre_sid", "dataset": "ds", "status": "updated",
            "changed_fields": ["description"], "taxonomy_affected": False,
        })

    # Client sees ownership without ever calling register_agent
    client = A2XClient(base_url="http://test", ownership_file=path)
    assert client._owned.contains("ds", "pre_sid")
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    # Should succeed without NotOwnedError
    client.update_agent("ds", "pre_sid", {"description": "new"})
    client.close()


# ── Context manager (M-INIT-007) ────────────────────────────────────────────

def test_context_manager_closes_transport(tmp_path):
    with A2XClient(base_url="http://test", ownership_file=tmp_path / "x.json") as client:
        transport_client = client._transport._client
        assert transport_client.is_closed is False
    assert transport_client.is_closed is True


# ── Connection error mapping ────────────────────────────────────────────────

def test_connection_error_wrapped(tmp_path):
    """All methods must surface A2XConnectionError, not bare httpx exceptions."""
    from src.client import A2XConnectionError

    def handler(req):
        raise httpx.ConnectError("refused")

    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(A2XConnectionError):
        client.list_agents("ds")
    client.close()


# ── Deregister additional cases ──────────────────────────────────────────────

def test_deregister_404_clears_local_then_reraises(tmp_path):
    """After PR-#3 contract: missing service → 404 (not 200+not_found).
    SDK must scrub local _owned and re-raise NotFoundError."""
    def handler(req):
        return httpx.Response(404, json={
            "detail": "Service 'sid' not found in dataset 'ds'",
        })
    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(NotFoundError):
        client.deregister_agent("ds", "sid")
    assert not client._owned.contains("ds", "sid")
    client.close()


def test_deregister_user_config_source_raises_specialized(tmp_path):
    from src.client import UserConfigServiceImmutableError

    def handler(req):
        return httpx.Response(400, json={
            "detail": "Cannot deregister user_config-sourced service"
        })

    client, _ = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    with pytest.raises(UserConfigServiceImmutableError):
        client.deregister_agent("ds", "sid")
    # _owned should stay — user_config service still exists server-side
    assert client._owned.contains("ds", "sid")
    client.close()


def test_deregister_twice_second_is_local_fail_fast(tmp_path):
    """After a successful deregister the sid is no longer owned → NotOwnedError."""
    def handler(req):
        return httpx.Response(200, json={"service_id": "sid", "status": "deregistered"})
    client, reqs = _make_client(handler, tmp_path)
    client._owned.add("ds", "sid")
    client.deregister_agent("ds", "sid")
    assert len(reqs) == 1

    with pytest.raises(NotOwnedError):
        client.deregister_agent("ds", "sid")
    # Still only one HTTP call — second attempt fail-fast.
    assert len(reqs) == 1
    client.close()


# ── delete_dataset additional cases ──────────────────────────────────────────

def test_delete_dataset_no_ownership_check(tmp_path):
    """delete_dataset is not gated by _owned — anyone who knows the name can delete."""
    def handler(req):
        return httpx.Response(200, json={"dataset": "foreign_ds", "status": "deleted"})
    client, _ = _make_client(handler, tmp_path)
    # _owned has nothing about "foreign_ds"
    r = client.delete_dataset("foreign_ds")
    assert r.status == "deleted"
    client.close()


# ── Response schema forward-compat ───────────────────────────────────────────

def test_register_response_tolerates_extra_fields(tmp_path):
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
            "future_field": "ok", "server_version": "1.2.3",
        })
    client, _ = _make_client(handler, tmp_path)
    r = client.register_agent("ds", {"name": "N", "description": "D"})
    assert r.service_id == "sid"
    client.close()


# ── property getters (L2) ───────────────────────────────────────────────────

def test_properties_reflect_init_values(tmp_path):
    client = A2XClient(
        base_url="http://test:9999",
        timeout=12.5,
        api_key="abc",
        ownership_file=tmp_path / "x.json",
    )
    assert client.base_url == "http://test:9999/"
    assert client.timeout == 12.5
    assert client.api_key == "abc"
    client.close()


@pytest.mark.parametrize("attr", ["timeout", "api_key"])
def test_properties_are_read_only(tmp_path, attr):
    client = A2XClient(base_url="http://test", ownership_file=tmp_path / "x.json")
    with pytest.raises(AttributeError):
        setattr(client, attr, "mutated")
    client.close()


# ── Corner cases that could silently regress ────────────────────────────────

def test_get_agent_without_content_type_header_raises(tmp_path):
    """Servers that omit Content-Type should trigger the fallback branch."""
    def handler(req):
        # Return content with no content-type. httpx may add a default, so we
        # explicitly remove it.
        resp = httpx.Response(200, content=b'{"id":"x"}')
        resp.headers.pop("content-type", None)
        return resp

    client, _ = _make_client(handler, tmp_path)
    with pytest.raises(UnexpectedServiceTypeError):
        client.get_agent("ds", "sid")
    client.close()


def test_agent_card_with_none_fields_passes_through(tmp_path):
    """Nested None values inside agent_card should reach the backend as-is."""
    captured = {}

    def handler(req):
        import json as _json
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })

    client, _ = _make_client(handler, tmp_path)
    card = {"name": "N", "description": "D", "url": None, "provider": None}
    client.register_agent("ds", card)
    # SDK must not strip nested None values — backend validates, not SDK.
    assert captured["body"]["agent_card"] == card
    client.close()


@pytest.mark.parametrize("raw_base_url", ["http://test", "http://test/"])
def test_base_url_without_trailing_slash_does_not_double(tmp_path, raw_base_url):
    """base_url gets / appended at most once; no // in the final request URL."""
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={
            "dataset": "ds", "embedding_model": "m",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })

    client = A2XClient(base_url=raw_base_url, ownership_file=tmp_path / "x.json")
    client._transport._client.close()
    client._transport._client = httpx.Client(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    client.create_dataset("ds", embedding_model="m")
    # Strip scheme then confirm no accidental double slashes before /api.
    assert "//api" not in seen["url"].replace("http://", "", 1)
    client.close()


def test_register_preserves_dict_identity(tmp_path):
    """SDK must not mutate the caller's agent_card dict."""
    def handler(req):
        return httpx.Response(200, json={
            "service_id": "sid", "dataset": "ds", "status": "registered",
        })
    client, _ = _make_client(handler, tmp_path)

    card = {"name": "N", "description": "D", "skills": [{"name": "a", "description": "b"}]}
    card_before = {k: (v.copy() if isinstance(v, list) else v) for k, v in card.items()}
    client.register_agent("ds", card)
    assert card == card_before, "SDK mutated the caller's agent_card dict"
    client.close()
