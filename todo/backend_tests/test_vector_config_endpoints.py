"""Tests for ``GET/POST /api/datasets/{ds}/vector-config`` after the
router → service refactor.

The router used to read/write ``vector_config.json`` itself with hard-
coded ``PROJECT_ROOT`` paths — meaning these endpoints couldn't be
tested against the fixture's tmp_path. After the refactor they go
through ``RegistryService.get_vector_config`` / ``set_vector_config``
which respect the injected ``database_dir``.
"""

from __future__ import annotations

import json


def _create_dataset(client, name: str, embedding_model: str = "all-MiniLM-L6-v2") -> None:
    r = client.post(
        "/api/datasets",
        json={"name": name, "embedding_model": embedding_model},
    )
    assert r.status_code == 200, r.text


def _vc_path(client, dataset: str):
    from src.backend.routers import dataset as dr
    return dr.get_registry_service()._database_dir / dataset / "vector_config.json"


# ── GET ──────────────────────────────────────────────────────────────────────

class TestGetVectorConfig:
    def test_returns_persisted_config(self, client):
        _create_dataset(client, "ds")
        r = client.get("/api/datasets/ds/vector-config")
        assert r.status_code == 200
        body = r.json()
        assert body["dataset"] == "ds"
        assert body["embedding_model"] == "all-MiniLM-L6-v2"
        assert isinstance(body["embedding_dim"], int)

    def test_missing_dataset_returns_default_no_write(self, client):
        """Read-side falls back to system default when file is missing,
        and does NOT create it (the write path is the only place to persist)."""
        r = client.get("/api/datasets/never_created/vector-config")
        assert r.status_code == 200
        body = r.json()
        assert body["embedding_model"] == "all-MiniLM-L6-v2"
        # File NOT created by the read
        assert not _vc_path(client, "never_created").exists()


# ── POST ─────────────────────────────────────────────────────────────────────

class TestSetVectorConfig:
    def test_known_model_sets_dim_automatically(self, client):
        """Switching to another known model in EMBEDDING_MODELS — dim auto-resolved."""
        _create_dataset(client, "ds")
        target = "paraphrase-multilingual-MiniLM-L12-v2"
        r = client.post(
            "/api/datasets/ds/vector-config",
            json={"embedding_model": target},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["embedding_model"] == target
        assert body["embedding_dim"] == 384  # from EMBEDDING_MODELS table
        cfg = json.loads(_vc_path(client, "ds").read_text(encoding="utf-8"))
        assert cfg["embedding_model"] == target

    def test_unknown_model_without_dim_returns_400(self, client):
        _create_dataset(client, "ds")
        r = client.post(
            "/api/datasets/ds/vector-config",
            json={"embedding_model": "made-up-model"},
        )
        assert r.status_code == 400
        assert "embedding_dim" in r.json()["detail"]

    def test_unknown_model_with_explicit_dim_works(self, client):
        _create_dataset(client, "ds")
        r = client.post(
            "/api/datasets/ds/vector-config",
            json={"embedding_model": "exotic-model", "embedding_dim": 1024},
        )
        assert r.status_code == 200
        cfg = json.loads(_vc_path(client, "ds").read_text(encoding="utf-8"))
        assert cfg == {"embedding_model": "exotic-model", "embedding_dim": 1024}

    def test_get_after_set_returns_persisted_value(self, client):
        _create_dataset(client, "ds")
        target = "paraphrase-multilingual-MiniLM-L12-v2"
        client.post(
            "/api/datasets/ds/vector-config",
            json={"embedding_model": target},
        )
        body = client.get("/api/datasets/ds/vector-config").json()
        assert body["embedding_model"] == target
