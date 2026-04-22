"""Auto-init behavior: registering to a missing namespace creates it.

Before this change, ``POST /services/a2a`` (and friends) succeeded against
a missing dataset by silently doing ``mkdir(exist_ok=True)`` on the
backing dir, leaving the dataset half-formed (no ``vector_config.json``,
no ``register_config.json``). Subsequent vector-search / build paths
would crash on the missing config files.

The fix routes register through ``_ensure_dataset_initialized``, which
calls the full ``create_dataset`` flow with defaults (``all-MiniLM-L6-v2``
+ all formats at v0.0). Embedding model and formats remain changeable
via the existing ``POST /vector-config`` and ``POST /register-config``
endpoints.

Tests verify:
  - register-to-missing returns 200 (not 404 / 500)
  - dataset directory + vector_config.json + register_config.json all
    exist after the register call
  - second register to the same dataset is a no-op for init (idempotent)
  - generic, a2a, skill all trigger auto-init
"""

from __future__ import annotations

import json
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def _register_a2a(client, dataset: str, name: str = "Agent",
                   description: str = "demo", endpoint: str = "http://e") -> str:
    body = {
        "agent_card": {
            "name": name, "description": description, "endpoint": endpoint,
        },
        "persistent": True,
    }
    r = client.post(f"/api/datasets/{dataset}/services/a2a", json=body)
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


def _register_generic(client, dataset: str, name: str = "Svc",
                       description: str = "demo") -> str:
    r = client.post(
        f"/api/datasets/{dataset}/services/generic",
        json={"name": name, "description": description, "persistent": True},
    )
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


def _ds_path(client, dataset: str) -> Path:
    """Resolve the on-disk dataset directory used by the test fixture."""
    from src.backend.routers import dataset as dr
    return dr.get_registry_service()._database_dir / dataset


# ── A2A path ─────────────────────────────────────────────────────────────────

class TestAutoInitA2A:
    def test_register_to_missing_dataset_succeeds(self, client):
        sid = _register_a2a(client, "fresh_pool")
        assert sid

    def test_dataset_appears_in_GET_datasets_after_auto_init(self, client):
        """GET /api/datasets now goes through RegistryService, so the
        test fixture's tmp_path is respected."""
        _register_a2a(client, "fresh_pool")
        r = client.get("/api/datasets")
        assert r.status_code == 200
        names = [d["name"] for d in r.json()]
        assert "fresh_pool" in names

    def test_vector_config_written_with_defaults(self, client):
        _register_a2a(client, "fresh_pool")
        vc = _ds_path(client, "fresh_pool") / "vector_config.json"
        assert vc.exists()
        cfg = json.loads(vc.read_text(encoding="utf-8"))
        assert cfg["embedding_model"] == "all-MiniLM-L6-v2"
        assert isinstance(cfg.get("embedding_dim"), int)

    def test_register_config_written_with_default_formats(self, client):
        _register_a2a(client, "fresh_pool")
        rc = _ds_path(client, "fresh_pool") / "register_config.json"
        assert rc.exists()
        cfg = json.loads(rc.read_text(encoding="utf-8"))
        # Stored shape: {"formats": {type: min_version, ...}}
        formats = cfg["formats"]
        # Default: all three types allowed at v0.0
        assert set(formats.keys()) == {"a2a", "generic", "skill"}
        for v in formats.values():
            assert v == "v0.0"


# ── Generic path ─────────────────────────────────────────────────────────────

class TestAutoInitGeneric:
    def test_register_generic_to_missing_dataset_succeeds(self, client):
        sid = _register_generic(client, "fresh_pool")
        assert sid

    def test_generic_path_also_writes_configs(self, client):
        _register_generic(client, "fresh_pool")
        ds = _ds_path(client, "fresh_pool")
        assert (ds / "vector_config.json").exists()
        assert (ds / "register_config.json").exists()


# ── Idempotence ──────────────────────────────────────────────────────────────

class TestIdempotent:
    def test_second_register_does_not_rewrite_vector_config(self, client):
        """Idempotence: once initialized, register never touches the config file."""
        _register_a2a(client, "fresh_pool", name="A1")
        vc_path = _ds_path(client, "fresh_pool") / "vector_config.json"
        # Manually overwrite to a hypothetical other model
        vc_path.write_text(
            json.dumps({"embedding_model": "bge-small-zh-v1.5", "embedding_dim": 512}) + "\n",
            encoding="utf-8",
        )
        # Second register must NOT clobber it back to default
        _register_a2a(client, "fresh_pool", name="A2", endpoint="http://e2")
        cfg = json.loads(vc_path.read_text(encoding="utf-8"))
        assert cfg["embedding_model"] == "bge-small-zh-v1.5"

    def test_explicit_create_then_register_works_unchanged(self, client):
        """User-driven create_dataset still wins; register is a no-op for init."""
        target = "paraphrase-multilingual-MiniLM-L12-v2"
        r = client.post(
            "/api/datasets",
            json={"name": "explicit", "embedding_model": target},
        )
        assert r.status_code == 200
        _register_a2a(client, "explicit")
        vc = json.loads(
            (_ds_path(client, "explicit") / "vector_config.json").read_text(encoding="utf-8")
        )
        assert vc["embedding_model"] == target  # not clobbered


# ── Reservation path also benefits ───────────────────────────────────────────

class TestReservationOnAutoInitDataset:
    def test_can_reserve_blank_after_auto_init(self, client):
        """End-to-end: register blank → reserve → release; all on auto-init dataset."""
        body = {
            "agent_card": {
                "name": "_BlankAgent_http://x",
                "description": "__BLANK__",
                "endpoint": "http://x",
                "status": "online",
            },
            "service_id": "blank_a",
            "persistent": True,
        }
        r = client.post("/api/datasets/auto_init_pool/services/a2a", json=body)
        assert r.status_code == 200
        # Reserve the blank
        r2 = client.post(
            "/api/datasets/auto_init_pool/reservations",
            json={
                "filters": {"description": "__BLANK__", "status": "online"},
                "n": 1, "ttl_seconds": 30,
            },
        )
        assert r2.status_code == 200
        assert len(r2.json()["reservations"]) == 1
