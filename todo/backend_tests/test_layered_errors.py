"""End-to-end tests for the RegistryNotFoundError → HTTP 404 layered contract.

Verifies that ``RegistryService`` raises ``RegistryNotFoundError`` for missing
resources and that the FastAPI router maps it to a clean 404 (not 500, not
200+status="not_found").
"""

from __future__ import annotations


def _create_dataset(client, name: str) -> None:
    r = client.post("/api/datasets",
                    json={"name": name, "embedding_model": "all-MiniLM-L6-v2"})
    assert r.status_code == 200, r.text


def _register_a2a(client, dataset: str, name: str, sid: str) -> None:
    r = client.post(
        f"/api/datasets/{dataset}/services/a2a",
        json={
            "agent_card": {"name": name, "description": "d", "endpoint": "http://x"},
            "service_id": sid,
            "persistent": True,
        },
    )
    assert r.status_code == 200, r.text


# ── Deregister now raises (was: 200 + status='not_found') ────────────────────

class TestDeregisterMissing:
    def test_missing_service_returns_404(self, client):
        _create_dataset(client, "ds")
        r = client.delete("/api/datasets/ds/services/never_registered")
        assert r.status_code == 404
        # Detail message should name the sid + dataset
        assert "never_registered" in r.json()["detail"]
        assert "ds" in r.json()["detail"]

    def test_missing_service_in_missing_dataset_also_404(self, client):
        r = client.delete("/api/datasets/no_such_dataset/services/no_such_sid")
        assert r.status_code == 404

    def test_existing_service_returns_200_with_deregistered_status(self, client):
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", "Agent", "agent_keep")
        r = client.delete("/api/datasets/ds/services/agent_keep")
        assert r.status_code == 200
        body = r.json()
        assert body["service_id"] == "agent_keep"
        assert body["status"] == "deregistered"

    def test_double_deregister_second_is_404(self, client):
        """First call succeeds; second call (sid now gone) is 404, not 200."""
        _create_dataset(client, "ds")
        _register_a2a(client, "ds", "Agent", "agent_dup")
        r1 = client.delete("/api/datasets/ds/services/agent_dup")
        assert r1.status_code == 200
        r2 = client.delete("/api/datasets/ds/services/agent_dup")
        assert r2.status_code == 404


# ── Update on missing service ────────────────────────────────────────────────

class TestUpdateMissing:
    def test_update_missing_service_returns_404(self, client):
        _create_dataset(client, "ds")
        r = client.put(
            "/api/datasets/ds/services/never_registered",
            json={"description": "new"},
        )
        assert r.status_code == 404
        assert "never_registered" in r.json()["detail"]

    def test_update_missing_service_no_dataset_returns_404(self, client):
        r = client.put(
            "/api/datasets/no_ds/services/no_sid",
            json={"description": "new"},
        )
        assert r.status_code == 404


# ── Single-mode get on missing service ───────────────────────────────────────

class TestSingleMissing:
    def test_get_missing_service_returns_404_with_detail(self, client):
        _create_dataset(client, "ds")
        r = client.get("/api/datasets/ds/services/no_such_sid")
        assert r.status_code == 404
        # Detail now names the sid + dataset (PR-#3 improvement)
        assert "no_such_sid" in r.json()["detail"]
        assert "ds" in r.json()["detail"]


# ── ValueError still maps to 400 (not impacted) ──────────────────────────────

class TestValueErrorStill400:
    def test_create_duplicate_dataset_is_400(self, client):
        _create_dataset(client, "ds")
        r = client.post(
            "/api/datasets",
            json={"name": "ds", "embedding_model": "all-MiniLM-L6-v2"},
        )
        # Backend uses ValueError for "already exists" → router maps to 400
        assert r.status_code == 400
