"""Distributed sync module for A2X Registry.

Opt-in: a registry instance runs standalone until ``a2x-registry cluster
init`` creates ``cluster_state.json``. Once initialized, instances that
become reachable sync their flat registry (CRUD/list/get/filter) so a
query to any instance returns every reachable instance's services;
instances that drift apart drop each other's records via beacon-lease
expiry.

Design model (see ``docs/cluster_design.md``):
  - AP / eventually-consistent, gossip replication with LWW versioning.
  - origin-only writes; external records are read-only, memory-only replicas.
  - loop-free via split-horizon + strict version dedup.

Public API:
    - ``ClusterStore`` — the single stateful object for this instance
    - ``ClusterState`` / ``ClusterConfig`` — persisted state / tuning
    - ``get_cluster_store`` / ``set_cluster_store`` — module singleton hooks
    - ``router`` — FastAPI router for ``/api/cluster/*``
"""

from .config import ClusterConfig
from .state import ClusterState
from .store import ClusterStore
from .deps import get_cluster_store, set_cluster_store
from .router import router

__all__ = [
    "ClusterConfig",
    "ClusterState",
    "ClusterStore",
    "get_cluster_store",
    "set_cluster_store",
    "router",
]
