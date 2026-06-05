"""Cluster runtime tuning knobs.

A single immutable config object passed to ``ClusterStore`` and the
background daemons. Values are conservative defaults suitable for a small
group of intermittently-connected registry instances.

Operators can override any knob at deploy time via ``A2X_REGISTRY_CLUSTER_*``
environment variables (read by ``ClusterConfig.from_env`` at server start) —
no code change needed. A malformed value logs a warning and falls back to
the default.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields

logger = logging.getLogger(__name__)


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

    # ── env-var overrides ────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "ClusterConfig":
        """Build a config from defaults, overriding any knob present as an
        ``A2X_REGISTRY_CLUSTER_<FIELD>`` env var (e.g.
        ``A2X_REGISTRY_CLUSTER_BEACON_TTL=10``). Unknown/blank vars keep the
        default; a non-numeric value logs a warning and keeps the default.
        """
        defaults = cls()
        overrides = {}
        for f in fields(cls):
            env_name = f"A2X_REGISTRY_CLUSTER_{f.name.upper()}"
            raw = os.environ.get(env_name, "").strip()
            if not raw:
                continue
            # With `from __future__ import annotations`, f.type is the string
            # "int"/"float". int fields tolerate "10" or "10.0".
            try:
                overrides[f.name] = int(float(raw)) if f.type == "int" else float(raw)
            except (ValueError, TypeError):
                logger.warning(
                    "cluster: ignoring invalid %s=%r (using default %s)",
                    env_name, raw, getattr(defaults, f.name),
                )
        return cls(**overrides) if overrides else defaults
