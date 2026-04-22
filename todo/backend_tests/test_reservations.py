"""End-to-end tests for the reservation lease endpoints.

Covers the 5 routes added in Commit C:
  POST   /reservations
  DELETE /reservations/{holder_id}
  DELETE /reservations/{holder_id}/{sid}
  POST   /reservations/{holder_id}/extend
  DELETE /services/{sid}/lease            (teammate-self)
"""

from __future__ import annotations


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_dataset(client, name: str) -> None:
    r = client.post("/api/datasets",
                    json={"name": name, "embedding_model": "all-MiniLM-L6-v2"})
    assert r.status_code == 200, r.text


def _register_blank(client, dataset: str, sid: str, endpoint: str = "http://x") -> str:
    body = {
        "agent_card": {
            "name": f"_BlankAgent_{endpoint}",
            "description": "__BLANK__",
            "endpoint": endpoint,
            "status": "online",
        },
        "service_id": sid,
        "persistent": True,
    }
    r = client.post(f"/api/datasets/{dataset}/services/a2a", json=body)
    assert r.status_code == 200, r.text
    return r.json()["service_id"]


def _reserve(client, dataset: str, **body):
    body.setdefault("filters", {"description": "__BLANK__", "status": "online"})
    body.setdefault("n", 1)
    body.setdefault("ttl_seconds", 30)
    r = client.post(f"/api/datasets/{dataset}/reservations", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── Reserve ──────────────────────────────────────────────────────────────────

class TestReserve:
    def test_reserve_one_returns_holder_and_agent(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1", "http://a")
        body = _reserve(client, "ds")
        assert body["holder_id"].startswith("holder_")
        assert body["ttl_seconds"] == 30
        assert body["expires_at_unix"] > 0
        assert len(body["reservations"]) == 1
        assert body["reservations"][0]["id"] == "agent_1"

    def test_reserve_n_three_returns_three(self, client):
        _create_dataset(client, "ds")
        for i in range(3):
            _register_blank(client, "ds", f"agent_{i}", f"http://a{i}")
        body = _reserve(client, "ds", n=3)
        assert len(body["reservations"]) == 3

    def test_reserve_n_more_than_available_returns_what_exists(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_only")
        body = _reserve(client, "ds", n=5)
        assert len(body["reservations"]) == 1

    def test_reserve_skips_already_leased(self, client):
        _create_dataset(client, "ds")
        for i in range(2):
            _register_blank(client, "ds", f"agent_{i}", f"http://a{i}")
        body1 = _reserve(client, "ds", n=1)
        body2 = _reserve(client, "ds", n=2)
        # body1's sid should NOT appear in body2's reservations
        sid1 = body1["reservations"][0]["id"]
        sids2 = [a["id"] for a in body2["reservations"]]
        assert sid1 not in sids2
        assert len(sids2) == 1  # only the unleased one

    def test_explicit_holder_id_used_verbatim(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds", holder_id="custom_holder")
        assert body["holder_id"] == "custom_holder"

    def test_reserve_invalid_n_returns_400(self, client):
        _create_dataset(client, "ds")
        r = client.post("/api/datasets/ds/reservations",
                        json={"filters": {}, "n": -1, "ttl_seconds": 30})
        assert r.status_code == 400

    def test_reserved_agents_hidden_from_default_list(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        _reserve(client, "ds")
        # default list (include_leased=false) should NOT show the reserved one
        r = client.get("/api/datasets/ds/services",
                       params={"description": "__BLANK__", "status": "online"})
        assert r.status_code == 200
        assert r.json() == []
        # include_leased=true exposes it
        r = client.get("/api/datasets/ds/services",
                       params={"description": "__BLANK__", "status": "online",
                               "include_leased": "true"})
        assert len(r.json()) == 1


# ── Release (leader bulk) ────────────────────────────────────────────────────

class TestReleaseBulk:
    def test_release_all_holder_leases(self, client):
        _create_dataset(client, "ds")
        for i in range(2):
            _register_blank(client, "ds", f"agent_{i}", f"http://a{i}")
        body = _reserve(client, "ds", n=2)
        holder = body["holder_id"]
        r = client.delete(f"/api/datasets/ds/reservations/{holder}")
        assert r.status_code == 200
        released = sorted(r.json()["released"])
        assert released == ["agent_0", "agent_1"]

    def test_bulk_release_idempotent(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds")
        holder = body["holder_id"]
        # First call frees it
        client.delete(f"/api/datasets/ds/reservations/{holder}")
        # Second call is no-op
        r2 = client.delete(f"/api/datasets/ds/reservations/{holder}")
        assert r2.status_code == 200
        assert r2.json()["released"] == []

    def test_bulk_release_unknown_holder_returns_empty(self, client):
        _create_dataset(client, "ds")
        r = client.delete("/api/datasets/ds/reservations/never_existed")
        assert r.status_code == 200
        assert r.json()["released"] == []


# ── Release (leader per-sid) ─────────────────────────────────────────────────

class TestReleasePerSid:
    def test_release_one_sid_only(self, client):
        _create_dataset(client, "ds")
        for i in range(2):
            _register_blank(client, "ds", f"agent_{i}", f"http://a{i}")
        body = _reserve(client, "ds", n=2)
        holder = body["holder_id"]
        r = client.delete(f"/api/datasets/ds/reservations/{holder}/agent_0")
        assert r.status_code == 200
        assert r.json()["released"] == ["agent_0"]
        # The other lease still exists — agent_1 is hidden from default list
        r2 = client.get("/api/datasets/ds/services",
                        params={"description": "__BLANK__"})
        ids = [e["id"] for e in r2.json()]
        assert "agent_0" in ids
        assert "agent_1" not in ids

    def test_release_missing_sid_returns_empty_no_error(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds")
        holder = body["holder_id"]
        r = client.delete(
            f"/api/datasets/ds/reservations/{holder}/never_leased_sid"
        )
        assert r.status_code == 200
        assert r.json()["released"] == []

    def test_release_other_holders_sid_returns_403(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds", holder_id="leader_a")
        # Different holder tries to release agent_1
        r = client.delete("/api/datasets/ds/reservations/leader_b/agent_1")
        assert r.status_code == 403


# ── Release (teammate-self) ──────────────────────────────────────────────────

class TestReleaseSelf:
    def test_release_self_drops_any_lease(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds", holder_id="leader_x")
        r = client.delete("/api/datasets/ds/services/agent_1/lease")
        assert r.status_code == 200
        assert r.json() == {"released": True, "prev_holder_id": "leader_x"}
        # Now appears in default list again
        r2 = client.get("/api/datasets/ds/services",
                        params={"description": "__BLANK__"})
        assert any(e["id"] == "agent_1" for e in r2.json())

    def test_release_self_no_lease_returns_false(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        # No reservation made
        r = client.delete("/api/datasets/ds/services/agent_1/lease")
        assert r.status_code == 200
        assert r.json() == {"released": False, "prev_holder_id": None}

    def test_release_self_idempotent(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        _reserve(client, "ds")
        client.delete("/api/datasets/ds/services/agent_1/lease")
        r = client.delete("/api/datasets/ds/services/agent_1/lease")
        assert r.status_code == 200
        assert r.json()["released"] is False


# ── Extend ───────────────────────────────────────────────────────────────────

class TestExtend:
    def test_extend_pushes_expiry_forward(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "agent_1")
        body = _reserve(client, "ds", ttl_seconds=10)
        first_expiry = body["expires_at_unix"]
        r = client.post(
            f"/api/datasets/ds/reservations/{body['holder_id']}/extend",
            json={"ttl_seconds": 60},
        )
        assert r.status_code == 200
        assert r.json()["expires_at_unix"] > first_expiry

    def test_extend_unknown_holder_returns_404(self, client):
        _create_dataset(client, "ds")
        r = client.post(
            "/api/datasets/ds/reservations/no_such_holder/extend",
            json={"ttl_seconds": 30},
        )
        assert r.status_code == 404
