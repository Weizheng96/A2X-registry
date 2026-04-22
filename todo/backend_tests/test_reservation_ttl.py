"""TTL expiry tests for reservation leases.

Uses ``unittest.mock.patch`` on ``time.monotonic`` (where the lease layer
reads it from) so we can advance the lease clock without sleeping. No real
``sleep`` calls — fast, deterministic, no flakes.
"""

from __future__ import annotations

from unittest.mock import patch


def _create_dataset(client, name: str) -> None:
    r = client.post("/api/datasets",
                    json={"name": name, "embedding_model": "all-MiniLM-L6-v2"})
    assert r.status_code == 200


def _register_blank(client, dataset: str, sid: str) -> None:
    r = client.post(
        f"/api/datasets/{dataset}/services/a2a",
        json={
            "agent_card": {
                "name": f"_BlankAgent_{sid}",
                "description": "__BLANK__",
                "endpoint": f"http://{sid}",
                "status": "online",
            },
            "service_id": sid,
            "persistent": True,
        },
    )
    assert r.status_code == 200


def _reserve(client, dataset: str, holder_id: str, ttl: int = 30) -> dict:
    r = client.post(
        f"/api/datasets/{dataset}/reservations",
        json={
            "filters": {"description": "__BLANK__", "status": "online"},
            "n": 1,
            "ttl_seconds": ttl,
            "holder_id": holder_id,
        },
    )
    assert r.status_code == 200
    return r.json()


class _Clock:
    """Mutable monotonic clock for ``patch`` substitution.

    We patch ``time.monotonic`` at the module ``src.register.service`` level
    (where the lease code reads it from), so unrelated callers of
    ``time.monotonic`` are unaffected.
    """
    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TestTTLExpiry:
    def test_expired_lease_reclaimed_by_next_reserve(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")

        clock = _Clock()
        with patch("src.register.service.time.monotonic", clock):
            _reserve(client, "ds", "leader_1", ttl=30)
            # 31s later, lease should be expired
            clock.advance(31)
            # leader_2 reserves successfully
            b2 = _reserve(client, "ds", "leader_2", ttl=30)
            assert len(b2["reservations"]) == 1
            assert b2["reservations"][0]["id"] == "blank_a"

    def test_release_after_expiry_silent_noop(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")
        clock = _Clock()
        with patch("src.register.service.time.monotonic", clock):
            body = _reserve(client, "ds", "leader_1", ttl=30)
            holder = body["holder_id"]
            clock.advance(31)
            # Bulk release after expiry is no-op (returns [])
            r = client.delete(f"/api/datasets/ds/reservations/{holder}")
            assert r.status_code == 200
            assert r.json()["released"] == []

    def test_extend_after_expiry_returns_404(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")
        clock = _Clock()
        with patch("src.register.service.time.monotonic", clock):
            body = _reserve(client, "ds", "leader_1", ttl=30)
            clock.advance(31)
            r = client.post(
                f"/api/datasets/ds/reservations/{body['holder_id']}/extend",
                json={"ttl_seconds": 60},
            )
            assert r.status_code == 404

    def test_reserved_returns_to_idle_pool_after_expiry(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")
        clock = _Clock()
        with patch("src.register.service.time.monotonic", clock):
            _reserve(client, "ds", "leader_1", ttl=30)
            # Default list excludes leased
            r1 = client.get("/api/datasets/ds/services",
                            params={"description": "__BLANK__"})
            assert r1.json() == []
            clock.advance(31)
            # After expiry, default list shows the agent again
            r2 = client.get("/api/datasets/ds/services",
                            params={"description": "__BLANK__"})
            assert len(r2.json()) == 1

    def test_self_release_works_near_expiry(self, client):
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")
        clock = _Clock()
        with patch("src.register.service.time.monotonic", clock):
            _reserve(client, "ds", "leader_1", ttl=30)
            clock.advance(29)  # just before expiry
            r = client.delete("/api/datasets/ds/services/blank_a/lease")
            assert r.status_code == 200
            assert r.json()["released"] is True
