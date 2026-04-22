"""Tests for ``GET /api/datasets`` after the router → service refactor.

The router used to walk ``PROJECT_ROOT / "database"`` directly with
``open()`` calls. Now it delegates to
``RegistryService.list_datasets_with_counts``, which means the test
fixture's tmp_path is respected and the I/O is uniformly handled by
the service layer.
"""

from __future__ import annotations

import json


def _create_dataset(client, name: str) -> None:
    r = client.post(
        "/api/datasets",
        json={"name": name, "embedding_model": "all-MiniLM-L6-v2"},
    )
    assert r.status_code == 200, r.text


def _register_a2a(client, dataset: str, name: str = "Agent") -> None:
    r = client.post(
        f"/api/datasets/{dataset}/services/a2a",
        json={
            "agent_card": {"name": name, "description": "d", "endpoint": "http://e"},
            "persistent": True,
        },
    )
    assert r.status_code == 200, r.text


class TestListDatasets:
    def test_empty_when_no_datasets(self, client):
        r = client.get("/api/datasets")
        assert r.status_code == 200
        assert r.json() == []

    def test_excludes_freshly_created_dataset_with_no_services(self, client):
        """Gating on service.json: an empty newly-created dataset has no
        service.json yet, so it's hidden from the listing until first
        register. (Behavior preserved from the pre-refactor router.)"""
        _create_dataset(client, "empty")
        r = client.get("/api/datasets")
        assert r.json() == []

    def test_includes_dataset_after_first_register(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds")
        r = client.get("/api/datasets")
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "ds"
        assert rows[0]["service_count"] == 1
        assert rows[0]["query_count"] == 0

    def test_service_count_reflects_register_calls(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", name="A")
        _register_a2a(client, "ds", name="B")
        _register_a2a(client, "ds", name="C")
        rows = client.get("/api/datasets").json()
        assert rows[0]["service_count"] == 3

    def test_query_count_picks_up_query_json(self, client, tmp_path):
        """Manually drop a query.json and verify count is reflected."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds")
        # Reach into the service's database_dir to plant a query file
        from src.backend.routers import dataset as dr
        db_dir = dr.get_registry_service()._database_dir
        query_dir = db_dir / "ds" / "query"
        query_dir.mkdir(parents=True, exist_ok=True)
        (query_dir / "query.json").write_text(
            json.dumps([{"q": "x"}, {"q": "y"}]) + "\n", encoding="utf-8",
        )
        rows = client.get("/api/datasets").json()
        [row] = rows
        assert row["query_count"] == 2

    def test_results_are_sorted_by_name(self, client):
        for name in ["zebra", "apple", "mango"]:
            _create_dataset(client, name)
            _register_a2a(client, name)
        rows = client.get("/api/datasets").json()
        assert [r["name"] for r in rows] == ["apple", "mango", "zebra"]

    def test_malformed_query_json_counts_as_zero_no_crash(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds")
        from src.backend.routers import dataset as dr
        db_dir = dr.get_registry_service()._database_dir
        query_dir = db_dir / "ds" / "query"
        query_dir.mkdir(parents=True, exist_ok=True)
        (query_dir / "query.json").write_text("not valid json {{{", encoding="utf-8")
        rows = client.get("/api/datasets").json()
        assert rows[0]["query_count"] == 0
        assert rows[0]["service_count"] == 1
