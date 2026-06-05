"""Multi-node integration: any node sees every node's services across
chain / ring / star topologies; separation evicts out-of-LAN records; an
evicted origin does not get resurrected by gossip.

All share one dataset "svc"; each node registers a uniquely-named service.
``converge`` drives anti-entropy to a fixed point.
"""

from __future__ import annotations

from a2x_registry.cluster.config import ClusterConfig

from .helpers import (
    FakeClock, FakeRegistry, InProcessTransport, beacon_flood, build_store,
    converge, visible,
)

DS = "svc"


def _node(tmp_path, transport, name, services, *, config=None, clock=None):
    reg = FakeRegistry()
    for s in services:
        reg.add_generic(DS, s)
    store = build_store(tmp_path, name, reg, transport, config=config, clock=clock)
    return store, reg


def _mutate(store, reg, name):
    sid = reg.add_generic(DS, name)
    store.on_local_mutation(DS, sid, "register", reg.get_entry(DS, sid))
    return sid


# ── full propagation across topologies ──────────────────────────────────

def test_chain_any_to_any(tmp_path):
    t = InProcessTransport()
    A, rA = _node(tmp_path, t, "A", ["a-svc"])
    B, rB = _node(tmp_path, t, "B", ["b-svc"])
    C, rC = _node(tmp_path, t, "C", ["c-svc"])
    B.connect_peer("A")
    B.connect_peer("C")
    converge([A, B, C])

    everyone = {"a-svc", "b-svc", "c-svc"}
    assert visible(A, rA, DS) == everyone
    assert visible(B, rB, DS) == everyone
    assert visible(C, rC, DS) == everyone  # C sees A's, learned transitively via B


def test_ring_any_to_any_and_terminates(tmp_path):
    t = InProcessTransport()
    A, rA = _node(tmp_path, t, "A", ["a-svc"])
    B, rB = _node(tmp_path, t, "B", ["b-svc"])
    C, rC = _node(tmp_path, t, "C", ["c-svc"])
    A.connect_peer("B")
    B.connect_peer("C")
    C.connect_peer("A")  # ring
    converge([A, B, C])

    everyone = {"a-svc", "b-svc", "c-svc"}
    for s, r in [(A, rA), (B, rB), (C, rC)]:
        assert visible(s, r, DS) == everyone

    # A new mutation floods the ring once and terminates (no hang).
    _mutate(A, rA, "a-extra")
    converge([A, B, C], rounds=2)
    assert "a-extra" in visible(C, rC, DS)


def test_star_any_to_any(tmp_path):
    t = InProcessTransport()
    B, rB = _node(tmp_path, t, "B", ["b-svc"])   # hub
    A, rA = _node(tmp_path, t, "A", ["a-svc"])
    C, rC = _node(tmp_path, t, "C", ["c-svc"])
    D, rD = _node(tmp_path, t, "D", ["d-svc"])
    B.connect_peer("A")
    B.connect_peer("C")
    B.connect_peer("D")
    converge([A, B, C, D])

    everyone = {"a-svc", "b-svc", "c-svc", "d-svc"}
    for s, r in [(A, rA), (B, rB), (C, rC), (D, rD)]:
        assert visible(s, r, DS) == everyone


def test_new_register_and_update_propagate_chain(tmp_path):
    t = InProcessTransport()
    A, rA = _node(tmp_path, t, "A", ["a-svc"])
    B, rB = _node(tmp_path, t, "B", ["b-svc"])
    C, rC = _node(tmp_path, t, "C", ["c-svc"])
    B.connect_peer("A")
    B.connect_peer("C")
    converge([A, B, C])

    # New registration at the far end (A) reaches the other far end (C).
    _mutate(A, rA, "a-new")
    converge([A, B, C], rounds=2)
    assert "a-new" in visible(C, rC, DS)

    # An update at C (new version of c-svc with changed description) reaches A.
    sid = rC.add_generic(DS, "c-svc", description="v2")
    from a2x_registry.cluster.state import make_key
    C._state.local_versions[make_key(DS, sid)] = [C._next_ts(), "C"]
    C.on_local_mutation(DS, sid, "update", rC.get_entry(DS, sid))
    converge([A, B, C], rounds=2)
    a_view = {r["wrapped"]["name"]: r["wrapped"]["description"]
              for r in A.foreign_rows(DS)}
    assert a_view.get("c-svc") == "v2"


# ── separation: evict out-of-LAN records ────────────────────────────────

def test_separation_evicts_departed_node(tmp_path):
    """Chain A-B-C; everyone converged. When all beacons stop (frozen time)
    the foreign replicas time out and are evicted — only local survives."""
    clk = FakeClock()
    t = InProcessTransport()
    cfg = ClusterConfig(beacon_ttl=30, beacon_grace=15)
    A, rA = _node(tmp_path, t, "A", ["a-svc"], config=cfg, clock=clk)
    B, rB = _node(tmp_path, t, "B", ["b-svc"], config=cfg, clock=clk)
    C, rC = _node(tmp_path, t, "C", ["c-svc"], config=cfg, clock=clk)
    B.connect_peer("A")
    B.connect_peer("C")
    converge([A, B, C])
    beacon_flood([A, B, C])
    assert visible(A, rA, DS) == {"a-svc", "b-svc", "c-svc"}

    # All beacons stop; advance past ttl+grace and sweep everywhere.
    clk.advance(100)
    for s in (A, B, C):
        s.sweep_origins()
    # Each node keeps only its own local service.
    assert visible(A, rA, DS) == {"a-svc"}
    assert visible(B, rB, DS) == {"b-svc"}
    assert visible(C, rC, DS) == {"c-svc"}


def test_only_departed_origin_evicted_others_stay(tmp_path):
    """A-B-C; C departs but B keeps beaconing. A evicts only C, keeps B."""
    clk = FakeClock()
    t = InProcessTransport()
    cfg = ClusterConfig(beacon_ttl=30, beacon_grace=15)
    A, rA = _node(tmp_path, t, "A", ["a-svc"], config=cfg, clock=clk)
    B, rB = _node(tmp_path, t, "B", ["b-svc"], config=cfg, clock=clk)
    C, rC = _node(tmp_path, t, "C", ["c-svc"], config=cfg, clock=clk)
    B.connect_peer("A")
    B.connect_peer("C")
    converge([A, B, C])
    beacon_flood([A, B, C])  # leases armed at t=0
    assert visible(A, rA, DS) == {"a-svc", "b-svc", "c-svc"}

    # t=20: B beacons again (renews lease[B] on A); C stays silent.
    clk.advance(20)
    B.emit_beacon()

    # t=46: C's lease (t=0 + 30 + 15 = 45) has expired; B's (20 + 45 = 65) hasn't.
    clk.advance(26)
    A.sweep_origins()
    assert "c-svc" not in visible(A, rA, DS)
    assert "b-svc" in visible(A, rA, DS)


def test_evicted_origin_not_resurrected_by_gossip(tmp_path):
    """The bug this guards: after A evicts C, anti-entropy with B (who hasn't
    evicted yet) must NOT re-pull C's records and ping-pong forever."""
    clk = FakeClock()
    t = InProcessTransport()
    cfg = ClusterConfig(beacon_ttl=30, beacon_grace=15)
    A, rA = _node(tmp_path, t, "A", ["a-svc"], config=cfg, clock=clk)
    B, rB = _node(tmp_path, t, "B", ["b-svc"], config=cfg, clock=clk)
    C, rC = _node(tmp_path, t, "C", ["c-svc"], config=cfg, clock=clk)
    # Triangle so A and B are directly connected too.
    A.connect_peer("B")
    B.connect_peer("C")
    A.connect_peer("C")
    converge([A, B, C])
    beacon_flood([A, B, C])  # leases for B and C armed at t=0
    assert "c-svc" in visible(A, rA, DS)

    # t=20: B keeps beaconing (stays alive); C is silent (departed).
    clk.advance(20)
    B.emit_beacon()

    # t=46: A evicts C (lease 0+45 expired) but keeps B (lease 20+45=65 alive),
    # so the A–B session survives. B has NOT swept yet → still holds c-svc.
    clk.advance(26)
    A.sweep_origins()
    assert "c-svc" not in visible(A, rA, DS)
    assert "b-svc" in visible(A, rA, DS)

    # A reconciles B (still holds c-svc) → suppression rejects the re-pull,
    # and B pushing it back is rejected too. No resurrection.
    A.reconcile(A._sessions["B"])
    B.reconcile(B._sessions["A"])
    assert "c-svc" not in visible(A, rA, DS)

    # Once B also evicts, the whole cluster is free of C.
    B.sweep_origins()
    converge([A, B], rounds=2)
    assert "c-svc" not in visible(A, rA, DS)
    assert "c-svc" not in visible(B, rB, DS)
