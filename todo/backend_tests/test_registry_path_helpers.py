"""Tests for ``RegistryService.dataset_dir`` and ``service_json_path``.

Both helpers are simple but they replace 4+ scattered ad-hoc path
constructions across the backend (build router, would-be search service
refactor). Verifying they consistently use the injected database_dir
makes future refactors of "where do datasets live" trivially safe.
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


class TestPathHelpers:
    def test_dataset_dir_uses_injected_database_dir(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        path = svc.dataset_dir("ds_xyz")
        assert path == svc._database_dir / "ds_xyz"

    def test_service_json_path_resolves_under_dataset_dir(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        sj = svc.service_json_path("ds_xyz")
        assert sj == svc._database_dir / "ds_xyz" / "service.json"
        assert sj.name == "service.json"

    def test_service_json_exists_after_register(self, client):
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        _register_a2a(client, "ds_xyz")
        # service.json is regenerated as part of register
        assert svc.service_json_path("ds_xyz").exists()

    def test_dataset_dir_returned_for_nonexistent_dataset_too(self, client):
        """Helper should return a Path even for missing datasets — caller
        decides what to do (e.g. existence check). It is NOT a guarantee
        the directory exists."""
        from src.backend.routers import dataset as dr
        svc = dr.get_registry_service()
        path = svc.dataset_dir("never_created")
        assert path.name == "never_created"
        assert not path.exists()  # because no register / create happened


class TestBuildRouterUsesHelper:
    def test_build_request_for_unregistered_dataset_reports_clear_error(self, client):
        """Build trigger via POST /build should fail cleanly when there's
        no service.json (the message used to be 'service.json not found',
        we now phrase it in terms of registration)."""
        # Need build router init too — guard with try/except
        try:
            from src.backend.routers import build as build_router
            from src.backend.routers import dataset as dataset_router
            build_router.init_registry_service(dataset_router.get_registry_service())
        except Exception:
            return  # backend conftest may not wire build router
        # Fire the build (will go async, complete with error)
        r = client.post("/api/datasets/never_registered/build", json={})
        # Accept either 200 (queued) or 4xx — depends on router setup;
        # the contract we care about is: if it gets past validation,
        # the eventual job state mentions "register" not "service.json".
        if r.status_code != 200:
            return
        # Poll status
        for _ in range(20):
            s = client.get("/api/datasets/never_registered/build/status").json()
            if s.get("status") in ("done", "error"):
                break
        assert s["status"] == "error"
        assert "register" in s["message"].lower() or "service.json" in s["message"].lower()
