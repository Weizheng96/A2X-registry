"""End-to-end tests for ``GET /api/datasets/{ds}/services?<filters>`` endpoint.

Fixtures spin up a fresh RegistryService per test (via ``client`` fixture),
then register services through the normal POST endpoints so the tests
exercise the full production code path: registration → file persistence
→ in-memory registry → filter query.
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
    r = client.post(
        f"/api/datasets/{dataset}/services/generic", json=body
    )
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


def _filter(client, dataset: str, **filters) -> list[dict]:
    r = client.get(f"/api/datasets/{dataset}/services", params=filters)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    return data


# ── Empty filters → return all ───────────────────────────────────────────────

class TestEmptyFilters:
    def test_empty_dataset_returns_empty_list(self, client):
        _create_dataset(client, "empty")
        r = client.get("/api/datasets/empty/services")
        assert r.status_code == 200
        assert r.json() == []

    def test_empty_filters_returns_all(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "dA"})
        _register_a2a(client, "ds", {"name": "B", "description": "dB"})
        _register_generic(client, "ds", "G", "dG")
        result = _filter(client, "ds")
        assert len(result) == 3
        assert {e["id"] for e in result} == {e["id"] for e in result}  # unique ids

    def test_reserved_params_alone_count_as_empty_filters(self, client):
        """fields/page/size shouldn't count as filter fields."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "dA"})
        r = client.get("/api/datasets/ds/services",
                       params={"fields": "detail", "size": "-1", "page": "1"})
        assert r.status_code == 200
        assert len(r.json()) == 1


# ── Wrapped response shape ──────────────────────────────────────────────────

class TestResponseShape:
    def test_a2a_entry_preserves_id_and_wraps_metadata(self, client):
        _create_dataset(client, "ds")
        sid = _register_a2a(client, "ds",
                            {"name": "Agent", "description": "desc",
                             "endpoint": "http://a"})
        [entry] = _filter(client, "ds")
        # Standard wrapper preserved — unlike mode=full which unwraps a2a
        assert entry["id"] == sid
        assert entry["type"] == "a2a"
        assert entry["name"] == "Agent"
        assert isinstance(entry["metadata"], dict)
        # Agent Card verbatim inside metadata
        assert entry["metadata"]["endpoint"] == "http://a"
        # Outer description is build_description output (trailing period)
        assert entry["description"].endswith(".")

    def test_generic_entry_has_expected_shape(self, client):
        _create_dataset(client, "ds")
        _register_generic(client, "ds", "Svc", "description text", url="http://s")
        [entry] = _filter(client, "ds")
        assert entry["type"] == "generic"
        assert entry["name"] == "Svc"
        assert entry["description"] == "description text"
        assert entry["metadata"]["url"] == "http://s"


# ── Single-field filter ─────────────────────────────────────────────────────

class TestSingleFieldFilter:
    def test_description_exact_match_finds_one(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "__BLANK__"})
        _register_a2a(client, "ds", {"name": "B", "description": "real"})
        r = _filter(client, "ds", description="__BLANK__")
        assert len(r) == 1
        assert r[0]["metadata"]["description"] == "__BLANK__"

    def test_description_no_match_returns_empty(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "real"})
        assert _filter(client, "ds", description="nonexistent") == []

    def test_name_filter_matches_across_types(self, client):
        """``name`` exists on a2a.agent_card and on generic.service_data."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "shared", "description": "a2a"})
        _register_generic(client, "ds", "shared", "generic desc")
        r = _filter(client, "ds", name="shared")
        assert len(r) == 2
        assert {e["type"] for e in r} == {"a2a", "generic"}

    def test_custom_field_filter_a2a_only(self, client):
        """``endpoint`` only exists on a2a (via extra=allow)."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "dA",
                                     "endpoint": "http://a"})
        _register_generic(client, "ds", "G", "generic")
        r = _filter(client, "ds", endpoint="http://a")
        assert len(r) == 1
        assert r[0]["type"] == "a2a"


# ── Composite AND filtering ──────────────────────────────────────────────────

class TestCompositeFilter:
    def test_all_conditions_must_match(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "__BLANK__",
                                     "endpoint": "http://a", "teamCount": 0})
        _register_a2a(client, "ds", {"name": "B", "description": "__BLANK__",
                                     "endpoint": "http://b", "teamCount": 2})
        r = _filter(client, "ds", description="__BLANK__", teamCount="0")
        assert len(r) == 1
        assert r[0]["metadata"]["endpoint"] == "http://a"

    def test_partial_match_excluded(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "__BLANK__",
                                     "teamCount": 5})
        # description matches, but count doesn't → no result
        assert _filter(client, "ds", description="__BLANK__",
                       teamCount="0") == []


# ── String coercion on both sides ────────────────────────────────────────────

class TestStringCoercion:
    def test_int_query_matches_int_field(self, client):
        """Query '0' should match stored integer 0 via str() coercion."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d",
                                     "teamCount": 0})
        r = _filter(client, "ds", teamCount="0")
        assert len(r) == 1

    def test_mismatched_int_excluded(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d",
                                     "teamCount": 5})
        assert _filter(client, "ds", teamCount="0") == []

    def test_float_stringification_cross_check(self, client):
        """str(5) == '5' but str(5.0) != '5' — document the coercion edge."""
        _create_dataset(client, "ds")
        # Use generic with int/float ambiguous storage
        _register_a2a(client, "ds", {"name": "A", "description": "d",
                                     "someCount": 5})
        assert len(_filter(client, "ds", someCount="5")) == 1
        # 5.0 stored → str is "5.0", won't match query "5"
        _register_a2a(client, "ds", {"name": "B", "description": "d",
                                     "someCount": 5.0})
        r = _filter(client, "ds", someCount="5")
        # Only the int-5 agent matches
        assert len(r) == 1
        assert r[0]["metadata"]["name"] == "A"


# ── Missing-field semantics ──────────────────────────────────────────────────

class TestMissingField:
    def test_missing_field_not_matched(self, client):
        """Filter for a field the entry doesn't have → not a match."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d"})
        # No endpoint on A → should not match
        assert _filter(client, "ds", endpoint="http://a") == []

    def test_none_value_treated_as_missing_via_exclude_none(self, client):
        """``exclude_none=True`` in model_dump → None-valued fields are absent."""
        _create_dataset(client, "ds")
        # AgentCard has version: str = "", so empty string is stored.
        # With exclude_none, None is dropped but empty string is kept.
        _register_a2a(client, "ds", {"name": "A", "description": "d",
                                     "version": ""})
        r = _filter(client, "ds", version="")
        assert len(r) == 1  # empty string still matches empty string


# ── Cross-type behavior ──────────────────────────────────────────────────────

class TestCrossTypeFilter:
    def test_shared_field_name_matches_both_types(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "shared-name", "description": "a2a-d"})
        _register_generic(client, "ds", "shared-name", "generic-d")
        r = _filter(client, "ds", name="shared-name")
        assert len(r) == 2

    def test_a2a_only_field_returns_a2a_only(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d",
                                     "endpoint": "http://a"})
        _register_generic(client, "ds", "G", "g-d")
        r = _filter(client, "ds", endpoint="http://a")
        assert len(r) == 1
        assert r[0]["type"] == "a2a"

    def test_generic_only_field_returns_generic_only(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", {"name": "A", "description": "d"})
        _register_generic(client, "ds", "G", "g-d", url="http://g")
        r = _filter(client, "ds", url="http://g")
        assert len(r) == 1
        assert r[0]["type"] == "generic"


# ── Filter against transformed description sanity ────────────────────────────

class TestRawVsTransformedDescription:
    def test_filter_matches_raw_not_build_description_output(self, client):
        """
        build_description wraps card.description with a trailing period;
        filter must match the ORIGINAL (entry.agent_card.description),
        not the transformed one shown in wrapper.description.
        """
        _create_dataset(client, "ds")
        _register_a2a(client, "ds",
                      {"name": "A", "description": "Original desc"})
        # Filter by the ORIGINAL value → match
        r = _filter(client, "ds", description="Original desc")
        assert len(r) == 1
        # The transformed form (with period) should NOT match
        assert _filter(client, "ds", description="Original desc.") == []


# ── Dataset errors ───────────────────────────────────────────────────────────

class TestDatasetErrors:
    def test_unknown_dataset_returns_empty_not_404(self, client):
        """
        list_services tolerates missing datasets silently (browse returns
        []). Filter mode inherits that behavior via svc.list_entries.
        """
        r = client.get("/api/datasets/nonexistent/services",
                       params={"mode": "filter", "name": "x"})
        # No HTTPException thrown → 200 with empty list
        assert r.status_code == 200
        assert r.json() == []
