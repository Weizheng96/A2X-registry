"""Tests for the path-resolution refactor in SearchService.

Before: SearchService had its own ``PROJECT_ROOT = Path(__file__).parent.parent.parent.parent``
and constructed ``PROJECT_ROOT / "database" / dataset / ...`` paths in
6+ places — meaning tests against tmp_path couldn't reach this code path.

After: SearchService delegates to the bound RegistryService for all
path resolution. ``set_registry()`` is now a hard requirement; calling
the path-resolving helpers without it raises a clear error.
"""

from __future__ import annotations


def _create_dataset(client, name: str) -> None:
    r = client.post(
        "/api/datasets",
        json={"name": name, "embedding_model": "all-MiniLM-L6-v2"},
    )
    assert r.status_code == 200, r.text


def _register_a2a(client, dataset: str) -> None:
    r = client.post(
        f"/api/datasets/{dataset}/services/a2a",
        json={"agent_card": {"name": "A", "description": "d", "endpoint": "http://e"},
              "persistent": True},
    )
    assert r.status_code == 200, r.text


class TestRegistryPathHelpers:
    """The path helpers added to RegistryService for #2 cleanup."""

    def test_query_path(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        path = svc.query_path("ds")
        assert path == svc._database_dir / "ds" / "query" / "query.json"

    def test_taxonomy_paths(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        assert svc.taxonomy_dir("ds") == svc._database_dir / "ds" / "taxonomy"
        assert svc.taxonomy_path("ds") == svc._database_dir / "ds" / "taxonomy" / "taxonomy.json"
        assert svc.class_path("ds") == svc._database_dir / "ds" / "taxonomy" / "class.json"

    def test_chroma_dir_is_database_root_relative(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        # One chroma dir per database root, not per dataset
        assert svc.chroma_dir() == svc._database_dir / "chroma"


class TestSearchServiceUsesRegistryPaths:
    """resolve_dataset_paths / discover_datasets / read_vector_config now
    all go through the bound RegistryService."""

    def test_resolve_dataset_paths_uses_tmp_path(self, client):
        from src.backend.services.search_service import resolve_dataset_paths
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        paths = resolve_dataset_paths("any_dataset")
        # All four returned paths sit under the injected database_dir
        for key in ("service_path", "query_path", "taxonomy_path", "class_path"):
            assert str(paths[key]).startswith(str(svc._database_dir))

    def test_discover_datasets_finds_only_datasets_with_query_json(self, client):
        from src.backend.services.search_service import discover_datasets
        # Register one dataset → has service.json but NO query.json
        _create_dataset(client, "no_queries")
        _register_a2a(client, "no_queries")
        # discover_datasets gates on query/query.json — should be empty
        assert discover_datasets() == []

        # Now plant a query.json
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        qp = svc.query_path("no_queries")
        qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text("[]", encoding="utf-8")
        # Now visible
        assert "no_queries" in discover_datasets()

    def test_read_vector_config_returns_persisted_model(self, client):
        from src.backend.services.search_service import SearchService
        _create_dataset(client, "ds")
        # Default
        assert SearchService.read_vector_config("ds") == "all-MiniLM-L6-v2"
        # Override via the same /vector-config endpoint
        r = client.post(
            "/api/datasets/ds/vector-config",
            json={"embedding_model": "paraphrase-multilingual-MiniLM-L12-v2"},
        )
        assert r.status_code == 200
        assert (
            SearchService.read_vector_config("ds")
            == "paraphrase-multilingual-MiniLM-L12-v2"
        )

    def test_read_vector_config_for_missing_dataset_returns_default(self, client):
        from src.backend.services.search_service import SearchService
        # Never created
        assert SearchService.read_vector_config("never_created") == "all-MiniLM-L6-v2"


class TestRegistryRequired:
    """Calling the path helpers without set_registry() should fail loudly."""

    def test_resolve_dataset_paths_without_registry_raises(self):
        import pytest
        from src.backend.services import search_service as sm
        prev = sm._registry  # snapshot for restore
        sm._registry = None  # explicit unbind
        try:
            with pytest.raises(RuntimeError, match="set_registry"):
                sm.resolve_dataset_paths("any")
        finally:
            sm._registry = prev  # restore so subsequent tests aren't affected
