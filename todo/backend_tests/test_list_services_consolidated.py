"""Tests for the consolidated ``GET /services`` and path-based single-fetch.

Covers the post-consolidation API:
  - ``GET /api/datasets/{ds}/services`` — one list endpoint, no `mode` param
  - ``fields=brief|detail`` projection
  - Pagination via response headers (``X-Total-Count`` etc.)
  - ``GET /api/datasets/{ds}/services/{service_id}`` — path-based single fetch
"""

from __future__ import annotations


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_dataset(client, name: str, formats=None) -> None:
    body = {"name": name, "embedding_model": "all-MiniLM-L6-v2"}
    if formats is not None:
        body["formats"] = formats
    r = client.post("/api/datasets", json=body)
    assert r.status_code == 200, r.text


def _register_a2a(client, dataset: str, card: dict, service_id: str | None = None) -> str:
    body = {"agent_card": card, "persistent": True}
    if service_id is not None:
        body["service_id"] = service_id
    r = client.post(f"/api/datasets/{dataset}/services/a2a", json=body)
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


def _register_generic(client, dataset: str, name: str, description: str,
                       url: str = "", service_id: str | None = None) -> str:
    body = {"name": name, "description": description, "persistent": True}
    if url:
        body["url"] = url
    if service_id is not None:
        body["service_id"] = service_id
    r = client.post(f"/api/datasets/{dataset}/services/generic", json=body)
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


# ── fields=detail (default) ─────────────────────────────────────────────────

class TestFieldsDetail:
    def test_default_returns_full_wrapper_for_a2a(self, client):
        _create_dataset(client, "ds")
        sid = _register_a2a(client, "ds",
                            {"name": "Agent", "description": "d",
                             "endpoint": "http://e"})
        r = client.get("/api/datasets/ds/services")
        assert r.status_code == 200
        [entry] = r.json()
        # detail keys present uniformly for a2a
        assert entry["id"] == sid
        assert entry["type"] == "a2a"
        assert entry["name"] == "Agent"
        assert entry["description"].endswith(".")
        assert entry["metadata"]["endpoint"] == "http://e"
        assert entry["source"] in ("api_config", "user_config", "ephemeral")

    def test_detail_uniform_for_generic(self, client):
        _create_dataset(client, "ds")
        _register_generic(client, "ds", "Svc", "desc", url="http://s")
        r = client.get("/api/datasets/ds/services?fields=detail")
        assert r.status_code == 200
        [entry] = r.json()
        assert entry["type"] == "generic"
        assert entry["name"] == "Svc"
        assert entry["description"] == "desc"
        assert entry["metadata"]["url"] == "http://s"
        assert "source" in entry

    def test_invalid_fields_value_returns_400(self, client):
        _create_dataset(client, "ds")
        r = client.get("/api/datasets/ds/services?fields=garbage")
        assert r.status_code == 400
        assert "fields" in r.json()["detail"]


# ── fields=brief projection ──────────────────────────────────────────────────

class TestFieldsBrief:
    def test_brief_returns_only_three_fields(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds",
                      {"name": "A", "description": "d", "endpoint": "http://e"})
        r = client.get("/api/datasets/ds/services?fields=brief")
        assert r.status_code == 200
        [entry] = r.json()
        assert set(entry.keys()) == {"id", "name", "description"}

    def test_brief_works_across_types(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "dA"})
        _register_generic(client, "ds", "G", "dG")
        r = client.get("/api/datasets/ds/services?fields=brief")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2
        for row in rows:
            assert set(row.keys()) == {"id", "name", "description"}


# ── Pagination via response headers ──────────────────────────────────────────

class TestPaginationHeaders:
    def test_size_negative_one_returns_all_no_headers(self, client):
        _create_dataset(client, "ds")
        for i in range(3):
            _register_a2a(client, "ds", {"name": f"A{i}", "description": "d"})
        r = client.get("/api/datasets/ds/services?size=-1")
        assert r.status_code == 200
        assert len(r.json()) == 3
        # No pagination headers when size=-1
        assert "X-Total-Count" not in r.headers
        assert "X-Page" not in r.headers

    def test_size_positive_sets_pagination_headers(self, client):
        _create_dataset(client, "ds")
        for i in range(7):
            _register_a2a(client, "ds", {"name": f"A{i}", "description": "d"})
        r = client.get("/api/datasets/ds/services?size=3&page=1")
        assert r.status_code == 200
        assert len(r.json()) == 3
        assert r.headers["X-Total-Count"] == "7"
        assert r.headers["X-Page"] == "1"
        assert r.headers["X-Total-Pages"] == "3"
        assert r.headers["X-Page-Size"] == "3"

    def test_page_two_returns_next_slice(self, client):
        _create_dataset(client, "ds")
        for i in range(5):
            _register_a2a(client, "ds", {"name": f"A{i}", "description": "d"},
                          service_id=f"sid_{i:02d}")
        r1 = client.get("/api/datasets/ds/services?size=2&page=1")
        r2 = client.get("/api/datasets/ds/services?size=2&page=2")
        ids1 = [e["id"] for e in r1.json()]
        ids2 = [e["id"] for e in r2.json()]
        # Pages don't overlap and union covers prefix
        assert set(ids1).isdisjoint(set(ids2))
        assert r2.headers["X-Page"] == "2"
        assert r2.headers["X-Total-Pages"] == "3"

    def test_pagination_with_filter(self, client):
        _create_dataset(client, "ds")
        for i in range(4):
            _register_a2a(client, "ds",
                          {"name": f"A{i}", "description": "__BLANK__",
                           "endpoint": f"http://{i}"})
        _register_a2a(client, "ds",
                      {"name": "real", "description": "real", "endpoint": "http://r"})
        r = client.get("/api/datasets/ds/services",
                       params={"description": "__BLANK__", "size": 2, "page": 1})
        assert r.status_code == 200
        # Total = 4 blanks (the "real" one is filtered out before pagination)
        assert r.headers["X-Total-Count"] == "4"
        assert len(r.json()) == 2


# ── Single-fetch path endpoint ───────────────────────────────────────────────

class TestSingleFetchPath:
    def test_get_existing_a2a_returns_single_dict(self, client):
        _create_dataset(client, "ds")
        sid = _register_a2a(client, "ds",
                            {"name": "Agent", "description": "d",
                             "endpoint": "http://e"},
                            service_id="agent_x")
        r = client.get(f"/api/datasets/ds/services/{sid}")
        assert r.status_code == 200
        body = r.json()
        # NOT a list — single dict
        assert isinstance(body, dict)
        assert body["id"] == sid
        assert body["type"] == "a2a"
        assert body["metadata"]["endpoint"] == "http://e"

    def test_get_existing_generic_returns_single_dict(self, client):
        _create_dataset(client, "ds")
        sid = _register_generic(client, "ds", "G", "dG", url="http://g",
                                service_id="gen_x")
        r = client.get(f"/api/datasets/ds/services/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        assert body["type"] == "generic"
        assert body["metadata"]["url"] == "http://g"

    def test_missing_sid_returns_404_with_detail(self, client):
        _create_dataset(client, "ds")
        r = client.get("/api/datasets/ds/services/no_such_sid")
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert "no_such_sid" in detail
        assert "ds" in detail


# ── No `mode` param semantics — any unknown key is now a filter ──────────────

class TestModeParamGone:
    def test_mode_query_param_now_treated_as_filter(self, client):
        """`mode=filter` used to be a discriminator — now it's just a filter
        looking for entries whose ``mode`` field equals ``"filter"``. None
        such → empty list (no error)."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d"})
        r = client.get("/api/datasets/ds/services?mode=filter")
        assert r.status_code == 200
        # No service has a `mode` card field → nothing matches
        assert r.json() == []

    def test_no_mode_param_works_unchanged(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d"})
        r = client.get("/api/datasets/ds/services")
        assert r.status_code == 200
        assert len(r.json()) == 1
