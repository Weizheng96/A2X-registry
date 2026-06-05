"""Cluster runtime tuning knobs.

A single immutable config object passed to ``ClusterStore`` and the
background daemons. Values are conservative defaults suitable for a small
group of intermittently-connected registry instances; they can be
overridden when constructing the store (e.g. from ``cluster_state.json``'s
optional ``config`` block in a later milestone).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClusterConfig:
    # Liveness (BEACON) — origin lease TTL + grace before a peer's records
    # are evicted. Tombstone retention is derived as ttl + grace.
    beacon_ttl: int = 30
    beacon_grace: int = 15
    beacon_interval: float = 10.0      # how often we broadcast our own beacon

    # Direct-link keepalive / hold timer.
    keepalive_interval: float = 10.0
    hold_timeout: float = 30.0

    # Periodic anti-entropy reconciliation with a random peer.
    anti_entropy_interval: float = 20.0

    # Per-request HTTP timeout for peer calls (seconds).
    http_timeout: float = 5.0

    @property
    def tombstone_retention(self) -> float:
        """Local tombstones are kept at least this long (seconds) before GC,
        so a peer that was partitioned has already evicted its stale replica
        (via the same beacon lease) before we forget the deletion."""
        return float(self.beacon_ttl + self.beacon_grace)
