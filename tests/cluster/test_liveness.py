"""BEACON liveness: origin-lease eviction (direct + transitive) + keepalive."""

from __future__ import annotations

from a2x_registry.cluster.config import ClusterConfig

from .helpers import FakeRegistry, InProcessTransport, build_store


def _short_cfg():
    # Tiny windows so sweep_tick can be driven with explicit `now`.
    return ClusterConfig(beacon_ttl=10, beacon_grace=5, hold_timeout=10)


def test_beacon_renews_origin_lease_then_eviction_on_silence(tmp_path):
    t = InProcessTransport()
    rA, rB = FakeRegistry(), FakeRegistry()
    rB.add_generic("ds", "b-svc")
    A = build_store(tmp_path, "A", rA, t, config=_short_cfg())
    B = build_store(tmp_path, "B", rB, t, config=_short_cfg())
    A.connect_peer("B")  # A pulls B's record (origin lease armed at learn time)
    assert A.state_summary()["foreign_records"] == 1

    # B beacons → A's lease for B is renewed.
    B.emit_beacon()
    assert A.state_summary()["tracked_origins"] == 1

    # Beacons stop. Before grace deadline: still alive.
    A._origin_leases.sweep_tick(now=5.0)
    assert A.state_summary()["foreign_records"] == 1

    # The install used time.monotonic(); drive sweep far into the future so
    # ttl+grace have elapsed → B's records evicted.
    import time
    A.sweep_origins(now=time.monotonic() + 100)
    assert A.state_summary()["foreign_records"] == 0
    assert "B" not in {p["node_id"] for p in A.state_summary()["peers"]}


def test_transitive_eviction_when_upstream_drops(tmp_path):
    """A–B–C: C's records reach A via B's relay. When C goes silent, A
    evicts C's records by lease expiry (no topology bookkeeping)."""
    t = InProcessTransport()
    rA, rB, rC = FakeRegistry(), FakeRegistry(), FakeRegistry()
    for r in (rA, rB, rC):
        r.add_generic("ds", "seed")
    rC.add_generic("ds", "c-only")
    cfg = _short_cfg()
    A = build_store(tmp_path, "A", rA, t, config=cfg)
    B = build_store(tmp_path, "B", rB, t, config=cfg)
    C = build_store(tmp_path, "C", rC, t, config=cfg)
    B.connect_peer("A")
    B.connect_peer("C")
    # Propagate C's record to A through B via a beacon-less push.
    sid = rC.add_generic("ds", "c-push")
    C.on_local_mutation("ds", sid, "register", rC.get_entry("ds", sid))
    assert any(r["origin_id"] == "C" for r in A.foreign_wrapped("ds"))

    # C goes silent; A's lease for origin C expires → C's records evicted.
    import time
    A.sweep_origins(now=time.monotonic() + 100)
    assert all(r["origin_id"] != "C" for r in A.foreign_wrapped("ds"))


def test_beacon_dedup_and_self_ignore(tmp_path):
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    # Our own beacon echoed back is ignored.
    assert A.handle_beacon("X", {"origin_id": "A", "seq": 1})["accepted"] is False
    # First beacon for B accepted; replays/older dropped.
    assert A.handle_beacon("B", {"origin_id": "B", "seq": 5})["accepted"] is True
    assert A.handle_beacon("B", {"origin_id": "B", "seq": 5})["accepted"] is False
    assert A.handle_beacon("B", {"origin_id": "B", "seq": 3})["accepted"] is False
    assert A.handle_beacon("B", {"origin_id": "B", "seq": 6})["accepted"] is True


def test_keepalive_hold_drops_silent_peer(tmp_path):
    t = InProcessTransport()
    rA = FakeRegistry()
    rA.add_generic("ds", "seed")
    A = build_store(tmp_path, "A", rA, t, config=_short_cfg())
    B = build_store(tmp_path, "B", FakeRegistry(), t, config=_short_cfg())
    A.connect_peer("B")
    assert "B" in {p["node_id"] for p in A.state_summary()["peers"]}

    # Force the peer's last_seen far into the past → HOLD expired.
    A._sessions["B"].last_seen = 0.0
    import time
    dropped = A.check_hold(now=time.monotonic() + 1000)
    assert dropped == ["B"]
    assert A.state_summary()["peers"] == []


def test_keepalive_refreshes_hold(tmp_path):
    t = InProcessTransport()
    rA = FakeRegistry()
    rA.add_generic("ds", "seed")
    A = build_store(tmp_path, "A", rA, t, config=_short_cfg())
    B = build_store(tmp_path, "B", FakeRegistry(), t, config=_short_cfg())
    A.connect_peer("B")
    A._sessions["B"].last_seen = 0.0
    # A keepalive from B refreshes last_seen → not dropped.
    A.handle_keepalive("B")
    assert A.check_hold() == []
