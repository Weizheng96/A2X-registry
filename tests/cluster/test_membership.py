"""Declarative membership control plane: set add/remove, bootstrap, leave
old cluster, LWW convergence, restart-rejoin, isolation, and auth.

Uses the in-process multi-store harness. ``settle`` drives the reconcile
loop (connect/disconnect + record/membership deltas) to a fixed point.
"""

from __future__ import annotations

from a2x_registry.cluster.state import ClusterState
from a2x_registry.cluster.membership import MembershipStore

from .helpers import (
    FakeRegistry, InProcessTransport, build_store, converge, settle, visible,
)


class _Ctx:
    def __init__(self, is_admin=False, namespaces=None):
        self.is_admin = is_admin
        self.namespaces = namespaces


class _Auth:
    def __init__(self, tokens):
        self._tokens = tokens

    def authenticate(self, token):
        if token in self._tokens:
            return self._tokens[token]
        raise ValueError("bad token")


def _ids(show: dict) -> set:
    return {m["node_id"] for m in show["roster"]}


def test_set_add_bootstrap(tmp_path):
    """A brand-new member learns the cluster only via the imperative join
    push (it had no session before), then forms a full mesh."""
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    B = build_store(tmp_path, "B", FakeRegistry(), t)
    assert B.list_peers() == []  # not connected yet

    res = A.membership.set_add([{"address": "B"}])
    assert res["cluster_id"].startswith("clu-")
    assert all(r["ok"] for r in res["results"])

    # Both adopted the same cluster, rosters agree, mesh formed both ways.
    assert A.membership.cluster_id == B.membership.cluster_id
    assert _ids(A.membership.show()) == {"A", "B"} == _ids(B.membership.show())
    assert "B" in {p.node_id for p in A.list_peers()}
    assert "A" in {p.node_id for p in B.list_peers()}


def test_set_add_three_full_mesh_and_service_visible(tmp_path):
    t = InProcessTransport()
    rA, rB, rC = FakeRegistry(), FakeRegistry(), FakeRegistry()
    A = build_store(tmp_path, "A", rA, t)
    B = build_store(tmp_path, "B", rB, t)
    C = build_store(tmp_path, "C", rC, t)

    A.membership.set_add([{"address": "B"}, {"address": "C"}])
    settle([A, B, C])

    # All three in one cluster, every pair connected (full mesh).
    cid = A.membership.cluster_id
    for s in (A, B, C):
        assert s.membership.cluster_id == cid
        assert _ids(s.membership.show()) == {"A", "B", "C"}
    assert {p.node_id for p in A.list_peers()} == {"B", "C"}
    assert {p.node_id for p in B.list_peers()} == {"A", "C"}
    assert {p.node_id for p in C.list_peers()} == {"A", "B"}

    # A service registered on C is visible on A (direct broadcast, full mesh).
    sid = rC.add_generic("ds", "c-svc")
    C.on_local_mutation("ds", sid, "register", rC.get_entry("ds", sid))
    settle([A, B, C])
    assert "c-svc" in visible(A, rA, "ds")


def test_membership_lww_concurrent_converges(tmp_path):
    """Two adds for the same node converge to one cluster_id via LWW."""
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    B = build_store(tmp_path, "B", FakeRegistry(), t)
    M = build_store(tmp_path, "M", FakeRegistry(), t)

    A.membership.set_add([{"address": "M"}])  # M joins A's cluster
    B.membership.set_add([{"address": "M"}])  # then B pulls M into B's cluster
    settle([A, B, M])

    # M ends in exactly one cluster; whichever add had the higher version wins.
    assert M.membership.cluster_id in (A.membership.cluster_id, B.membership.cluster_id)
    # M's own record is the single authority for its membership.
    assert M.membership.cluster_id == B.membership.cluster_id  # B added M last


def test_set_remove_tombstone_propagates(tmp_path):
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    B = build_store(tmp_path, "B", FakeRegistry(), t)
    C = build_store(tmp_path, "C", FakeRegistry(), t)
    A.membership.set_add([{"address": "B"}, {"address": "C"}])
    settle([A, B, C])

    A.membership.set_remove([{"node_id": "C"}])
    settle([A, B, C])

    # C is gone from every roster, deterministically (no HOLD advance).
    assert "C" not in _ids(A.membership.show())
    assert "C" not in _ids(B.membership.show())
    assert "C" not in {p.node_id for p in A.list_peers()}
    assert "C" not in {p.node_id for p in B.list_peers()}
    # C reverted to standalone.
    assert C.membership.cluster_id is None


def test_leave_old_cluster_is_immediate(tmp_path):
    """When a node is pulled into a new cluster it actively leaves the old
    one (old members drop it now, not via HOLD)."""
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)   # cluster 1
    B = build_store(tmp_path, "B", FakeRegistry(), t)   # cluster 1 member
    X = build_store(tmp_path, "X", FakeRegistry(), t)   # cluster 2
    A.membership.set_add([{"address": "B"}])
    settle([A, B])
    cid1 = A.membership.cluster_id

    # X pulls B into cluster 2 → B leaves cluster 1.
    X.membership.set_add([{"address": "B"}])
    settle([A, B, X])

    assert B.membership.cluster_id == X.membership.cluster_id != cid1
    # A dropped B immediately (graceful leave), A is alone again.
    assert "B" not in _ids(A.membership.show())
    assert "B" not in {p.node_id for p in A.list_peers()}
    # B is now meshed with X.
    assert "X" in {p.node_id for p in B.list_peers()}


def test_restart_rejoin_from_persisted_state(tmp_path):
    """A node reloads cluster_id + last_roster from disk and auto-reconnects."""
    t = InProcessTransport()
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    B = build_store(tmp_path, "B", FakeRegistry(), t)
    A.membership.set_add([{"address": "B"}])
    settle([A, B])
    cid = A.membership.cluster_id

    # "Restart" A: rebuild store from the same state file, fresh membership.
    state = ClusterState.load(path=tmp_path / "A.json")
    assert state.cluster_id == cid
    assert {m["node_id"] for m in state.last_roster} == {"B"}
    A2 = ClusterStoreFromState(state, t)
    A2.membership = MembershipStore(A2, state)
    t.register("A", A2)  # re-register the address → replaces old A
    assert A2.membership.cluster_id == cid
    assert A2.list_peers() == []           # sessions are memory-only, lost
    A2.membership.reconcile_connections()  # first sweeper tick
    assert "B" in {p.node_id for p in A2.list_peers()}  # auto-reconnected


def test_forward_compat_old_state_loads_standalone(tmp_path):
    """A state file written before the membership feature loads fine."""
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "node_id": "reg-old", "version_clock": 5,
        "local_versions": {}, "tombstones": {},
    }), encoding="utf-8")
    state = ClusterState.load(path=p)
    assert state.cluster_id is None
    assert state.last_roster == []
    assert state.my_membership_version is None


def test_membership_records_isolated_from_service_read_path(tmp_path):
    t = InProcessTransport()
    rA, rB = FakeRegistry(), FakeRegistry()
    A = build_store(tmp_path, "A", rA, t)
    B = build_store(tmp_path, "B", rB, t)
    A.membership.set_add([{"address": "B"}])
    settle([A, B])

    # Membership lives only in the roster overlay — never leaks into the
    # service foreign overlay / read path / state summary foreign counts.
    for ds in ("ds", "__cluster__", A.membership.cluster_id):
        assert A.foreign_rows(ds) == []
        assert A.foreign_entry(ds, "A:x") is None
    assert A.state_summary()["foreign_records"] == 0


def test_join_requires_admin_token_when_auth_on(tmp_path):
    t = InProcessTransport()
    admin = "admin-tok"
    A = build_store(tmp_path, "A", FakeRegistry(), t)
    B = build_store(tmp_path, "B", FakeRegistry(), t,
                    auth_store=_Auth({admin: _Ctx(is_admin=True)}))

    # No token → B rejects the join (unauthorized), B stays standalone.
    res = A.membership.set_add([{"address": "B"}])
    assert not res["results"][0]["ok"]
    assert B.membership.cluster_id is None

    # Admin token → accepted.
    res = A.membership.set_add([{"address": "B"}], token=admin)
    assert res["results"][0]["ok"]
    assert B.membership.cluster_id == A.membership.cluster_id


# ── helper: rebuild a ClusterStore from an existing state (restart sim) ──

def ClusterStoreFromState(state, transport):
    from a2x_registry.cluster.store import ClusterStore
    from a2x_registry.cluster.config import ClusterConfig
    return ClusterStore(
        state, config=ClusterConfig(), registry_svc=FakeRegistry(),
        transport=transport, advertise=state.node_id,
        auth_store_getter=(lambda: None),
    )
