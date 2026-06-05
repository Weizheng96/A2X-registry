"""ClusterStore — the cluster module's single stateful object.

Holds node identity + persisted local version/tombstone state, and (in
later milestones) the in-memory foreign-record overlay, peer sessions and
origin-liveness leases. Created once at backend startup via
``load_or_none`` and exposed through the module singleton in ``deps.py``.

M0 scope: identity + opt-in load + read seams that return empty (so the
dataset-router merge in M5 can call them unconditionally) + a no-op local
mutation hook (replication lands in M2).
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

from .config import ClusterConfig
from .state import ClusterState

logger = logging.getLogger(__name__)


class ClusterStore:
    """Owns all cluster runtime state for this registry instance."""

    def __init__(
        self,
        state: ClusterState,
        config: Optional[ClusterConfig] = None,
        registry_svc=None,
    ) -> None:
        self._state = state
        self._config = config or ClusterConfig()
        self._registry = registry_svc
        self._lock = threading.Lock()

    @classmethod
    def load_or_none(
        cls,
        config: Optional[ClusterConfig] = None,
        registry_svc=None,
    ) -> Optional["ClusterStore"]:
        """Build the store from a persisted ``cluster_state.json``, or
        return ``None`` when the file is absent (cluster not initialized →
        feature stays dormant). This is what makes the module opt-in.

        Defensive: a missing OR unreadable/corrupt state file both yield
        ``None`` (the registry stays standalone) rather than crashing
        startup. The corrupt case is logged so an operator can fix it.
        """
        try:
            state = ClusterState.load()
        except Exception as exc:  # noqa: BLE001 — corrupt file must not crash boot
            logger.error(
                "cluster: failed to load cluster_state.json (%s); staying standalone", exc,
            )
            return None
        if state is None:
            return None
        return cls(state, config=config, registry_svc=registry_svc)

    # ── identity / config ───────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._state.node_id

    @property
    def config(self) -> ClusterConfig:
        return self._config

    # ── read seams (dataset router merge calls these; empty until M5) ────

    def foreign_wrapped(self, dataset: str) -> List[dict]:
        """Wrapped-output dicts for replicated records in ``dataset`` —
        the shape ``RegistryService.list_services`` returns, with a
        namespaced ``id`` (``origin_id:service_id``) plus ``origin_id``."""
        return []

    def foreign_entries(self, dataset: str) -> List:
        """Replicated ``RegistryEntry`` objects for filter-matching in the
        list endpoint. Empty until the replication milestones populate the
        overlay."""
        return []

    # ── local mutation hook (wired via RegistryService.set_on_mutation) ──

    def on_local_mutation(self, dataset: str, service_id: str, op: str, entry) -> None:
        """Called after every successful local CRUD. No-op until M2 wires
        in outbound replication."""
        return None

    # ── observability ───────────────────────────────────────────────────

    def state_summary(self) -> dict:
        """Human/debug snapshot for ``GET /api/cluster/state`` and the
        ``cluster status`` CLI."""
        with self._lock:
            return {
                "node_id": self.node_id,
                "peers": [],
                "namespaces": {},
                "foreign_records": 0,
                "local_records": len(self._state.local_versions),
                "tombstones": len(self._state.tombstones),
            }
