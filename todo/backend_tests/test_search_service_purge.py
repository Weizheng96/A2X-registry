"""Tests for ``SearchService.purge_dataset`` after the refactor.

The dataset-delete router used to inline the ChromaDB cleanup + reach
into ``search_service._lock`` / ``_vector_instances`` / ``_a2x_instances``
/ ``_traditional_instances`` — three private attrs across one private
lock. After the refactor the router calls ``search_service.purge_dataset``
and stays oblivious to the search-side cache layout.

These tests exercise purge_dataset directly (the only reasonable
boundary for the new method) plus a smoke test through the HTTP delete
endpoint.
"""

from __future__ import annotations

from src.backend.services.search_service import search_service


def _create_dataset(client, name: str) -> None:
    r = client.post(
        "/api/datasets",
        json={"name": name, "embedding_model": "all-MiniLM-L6-v2"},
    )
    assert r.status_code == 200, r.text


class TestPurgeDataset:
    def test_drops_cached_vector_instance(self, client):
        # Plant a fake instance in the cache, then purge
        with search_service._lock:
            search_service._vector_instances["myds"] = object()
            search_service._traditional_instances["myds"] = object()
            search_service._a2x_instances["myds_browse"] = object()
            search_service._a2x_instances["myds_full"] = object()
            search_service._a2x_instances["other_browse"] = object()  # unrelated

        search_service.purge_dataset("myds")

        with search_service._lock:
            assert "myds" not in search_service._vector_instances
            assert "myds" not in search_service._traditional_instances
            assert "myds_browse" not in search_service._a2x_instances
            assert "myds_full" not in search_service._a2x_instances
            # Other dataset's cache untouched
            assert "other_browse" in search_service._a2x_instances
            search_service._a2x_instances.pop("other_browse", None)  # cleanup

    def test_purge_unknown_dataset_is_noop(self, client):
        """Calling purge for a dataset that was never cached should not raise."""
        search_service.purge_dataset("never_existed")  # no error

    def test_purge_idempotent(self, client):
        with search_service._lock:
            search_service._vector_instances["dup"] = object()
        search_service.purge_dataset("dup")
        search_service.purge_dataset("dup")  # second call is no-op


# ── End-to-end smoke through the delete endpoint ─────────────────────────────

class TestDeleteEndpointRoutesThroughPurge:
    def test_delete_dataset_clears_search_cache(self, client):
        _create_dataset(client, "ds")
        # Plant a cache entry
        with search_service._lock:
            search_service._vector_instances["ds"] = object()
        # Hit the delete endpoint
        r = client.delete("/api/datasets/ds")
        assert r.status_code == 200
        # Cache cleared as a side effect
        with search_service._lock:
            assert "ds" not in search_service._vector_instances
