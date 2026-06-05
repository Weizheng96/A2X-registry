"""Peer/session model.

A ``Peer`` is an established sync session with another instance: its node
id, the base address to reach it, and the set of namespaces both sides
agreed to sync. Sessions are keyed by peer node id in the ClusterStore, so
re-handshaking the same peer updates (rather than duplicates) the session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


@dataclass
class Peer:
    node_id: str
    address: str
    namespaces: Set[str] = field(default_factory=set)
    # monotonic timestamp of the last inbound contact from this peer; drives
    # the direct-link HOLD timer.
    last_seen: float = 0.0

    def to_summary(self) -> dict:
        return {
            "node_id": self.node_id,
            "address": self.address,
            "namespaces": sorted(self.namespaces),
        }
