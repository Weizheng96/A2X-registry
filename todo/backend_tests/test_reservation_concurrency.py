"""Concurrency tests for reservation leases.

Validates that the filter-AND-claim phase is atomic — racing leaders never
both win the same agent. Uses ThreadPoolExecutor to fire concurrent
``POST /reservations`` calls against a small pool of blanks and verifies
the result is mutually exclusive.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


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


def _reserve_one(client, dataset: str, holder_id: str) -> dict:
    r = client.post(
        f"/api/datasets/{dataset}/reservations",
        json={
            "filters": {"description": "__BLANK__", "status": "online"},
            "n": 1,
            "ttl_seconds": 30,
            "holder_id": holder_id,
        },
    )
    assert r.status_code == 200
    return r.json()


class TestConcurrentReserve:
    def test_eight_leaders_racing_for_four_blanks_get_disjoint_sets(self, client):
        """Mutual exclusion: each blank goes to at most one leader."""
        _create_dataset(client, "ds")
        for i in range(4):
            _register_blank(client, "ds", f"blank_{i}")

        # 8 concurrent leaders trying to reserve 1 each.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_reserve_one, client, "ds", f"leader_{i}")
                for i in range(8)
            ]
            results = [f.result() for f in futures]

        # Aggregate the sids granted across all leaders.
        sids_granted = []
        for body in results:
            for agent in body["reservations"]:
                sids_granted.append(agent["id"])

        # Exactly 4 blanks → exactly 4 grants total
        assert len(sids_granted) == 4
        # All distinct
        assert len(set(sids_granted)) == 4

    def test_self_release_then_other_leader_can_reclaim(self, client):
        """leader_1 reserves; teammate self-releases; leader_2 immediately reserves."""
        _create_dataset(client, "ds")
        _register_blank(client, "ds", "blank_a")

        b1 = _reserve_one(client, "ds", "leader_1")
        sid = b1["reservations"][0]["id"]

        # Teammate self-release
        r = client.delete(f"/api/datasets/ds/services/{sid}/lease")
        assert r.status_code == 200

        # leader_2 can immediately reserve the same blank
        b2 = _reserve_one(client, "ds", "leader_2")
        assert b2["reservations"][0]["id"] == sid

    def test_partial_release_lets_other_leader_take_freed_slot(self, client):
        """leader_1 holds 2; releases 1; leader_2 takes the freed one."""
        _create_dataset(client, "ds")
        for i in range(2):
            _register_blank(client, "ds", f"blank_{i}")

        b1 = client.post(
            "/api/datasets/ds/reservations",
            json={
                "filters": {"description": "__BLANK__", "status": "online"},
                "n": 2,
                "ttl_seconds": 30,
                "holder_id": "leader_1",
            },
        ).json()
        held = sorted(a["id"] for a in b1["reservations"])
        assert len(held) == 2

        # leader_1 releases the first sid only
        client.delete(f"/api/datasets/ds/reservations/leader_1/{held[0]}")

        # leader_2 grabs it
        b2 = _reserve_one(client, "ds", "leader_2")
        assert b2["reservations"][0]["id"] == held[0]
