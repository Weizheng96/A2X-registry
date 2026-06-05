"""ClusterStore — the cluster module's single stateful object.

Holds node identity + persisted local version/tombstone state, the
in-memory foreign-record overlay, and peer sessions. Exposes two kinds of
methods:

  - **handlers** (``handle_open`` / ``serve_digest`` / ``serve_pull`` /
    ``serve_updates``) — invoked by the local FastAPI router when a peer
    calls us. Tests invoke them directly through an in-process transport.
  - **orchestration** (``connect_peer`` / ``reconcile`` / ``disconnect_peer``)
    — invoked locally; reach out to peers through the injected ``Transport``.

Replication model: origin-only writes, so the global identity of a record
is ``(dataset, origin_id, service_id)`` and there are no write-write
conflicts — LWW (version ``(updated_at_ms, node_id)``) just dedups/orders
versions of the same record. Foreign records are read-only and memory-only.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from .auth_handshake import authorize_namespaces
from .config import ClusterConfig
from .envelope import SyncEnvelope, Version, version_newer
from .peer import Peer
from .state import ClusterState, Tombstone, make_key, split_key
from .transport import HttpTransport, Transport, TransportError

logger = logging.getLogger(__name__)

# In-memory foreign-overlay key: (dataset, origin_id, service_id).
_Key = Tuple[str, str, str]


class ClusterStore:
    """Owns all cluster runtime state for this registry instance."""

    def __init__(
        self,
        state: ClusterState,
        config: Optional[ClusterConfig] = None,
        registry_svc=None,
        transport: Optional[Transport] = None,
        advertise: str = "",
        auth_store_getter=None,
    ) -> None:
        self._state = state
        self._config = config or ClusterConfig()
        self._registry = registry_svc
        self._transport = transport or HttpTransport(self._config.http_timeout)
        self._advertise = advertise
        self._auth_store_getter = auth_store_getter or (lambda: None)
        self._lock = threading.RLock()
        # (dataset, origin_id, sid) -> envelope (live or tombstone)
        self._foreign: Dict[_Key, SyncEnvelope] = {}
        # peer node_id -> Peer
        self._sessions: Dict[str, Peer] = {}

    @classmethod
    def load_or_none(
        cls,
        config: Optional[ClusterConfig] = None,
        registry_svc=None,
        transport: Optional[Transport] = None,
        advertise: str = "",
        auth_store_getter=None,
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
        return cls(
            state, config=config, registry_svc=registry_svc,
            transport=transport, advertise=advertise,
            auth_store_getter=auth_store_getter,
        )

    # ── identity / config ───────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._state.node_id

    @property
    def config(self) -> ClusterConfig:
        return self._config

    # ── versioning (monotonic, survives clock step-back) ────────────────

    def _next_ts(self) -> int:
        """Next version timestamp (ms). Caller holds ``self._lock``."""
        now_ms = time.time_ns() // 1_000_000
        ts = max(now_ms, self._state.version_clock + 1)
        self._state.version_clock = ts
        return ts

    def _ensure_local_versions(self) -> None:
        """Assign a version to every local-origin record that doesn't have
        one yet (e.g. records that existed before cluster init / before the
        mutation hook). Persists once if anything changed."""
        if self._registry is None:
            return
        with self._lock:
            changed = False
            for ds in self._registry.list_datasets():
                for entry in self._registry.list_entries(ds):
                    if entry.source == "ephemeral":
                        continue
                    k = make_key(ds, entry.service_id)
                    if k not in self._state.local_versions and k not in self._state.tombstones:
                        self._state.local_versions[k] = [self._next_ts(), self.node_id]
                        changed = True
            if changed:
                self._state.save()

    # ── index / envelope helpers ────────────────────────────────────────

    def _wrapped_map(self, dataset: str) -> Dict[str, dict]:
        if self._registry is None:
            return {}
        return {s["id"]: s for s in self._registry.list_services(dataset)}

    def _local_index(self, namespaces: Optional[List[str]]) -> Dict[_Key, Version]:
        """Versions of all local-origin live records + local tombstones,
        scoped to ``namespaces`` (None = all local datasets)."""
        self._ensure_local_versions()
        idx: Dict[_Key, Version] = {}
        if self._registry is None:
            return idx
        datasets = set(self._registry.list_datasets())
        scope = datasets if not namespaces else (datasets & set(namespaces))
        with self._lock:
            for ds in scope:
                for entry in self._registry.list_entries(ds):
                    if entry.source == "ephemeral":
                        continue
                    v = self._state.local_versions.get(make_key(ds, entry.service_id))
                    if v is not None:
                        idx[(ds, self.node_id, entry.service_id)] = tuple(v)
            for k, t in self._state.tombstones.items():
                ds, sid = split_key(k)
                if namespaces and ds not in namespaces:
                    continue
                idx[(ds, self.node_id, sid)] = tuple(t.version)
        return idx

    def _full_index(self, namespaces: Optional[List[str]]) -> Dict[_Key, Version]:
        """Local + foreign record versions, scoped to ``namespaces``."""
        idx = self._local_index(namespaces)
        with self._lock:
            for (ds, origin, sid), env in self._foreign.items():
                if namespaces and ds not in namespaces:
                    continue
                idx[(ds, origin, sid)] = tuple(env.version)
        return idx

    def _build_local_envelope(self, dataset: str, sid: str) -> Optional[SyncEnvelope]:
        with self._lock:
            k = make_key(dataset, sid)
            tomb = self._state.tombstones.get(k)
            if tomb is not None:
                return SyncEnvelope(
                    dataset=dataset, service_id=sid, origin_id=self.node_id,
                    version=tuple(tomb.version), tombstone=True, payload=None,
                )
            v = self._state.local_versions.get(k)
        if v is None or self._registry is None:
            return None
        entry = self._registry.get_entry(dataset, sid)
        if entry is None:
            return None
        wrapped = self._wrapped_map(dataset).get(sid)
        payload = {"entry": entry.model_dump(mode="json"), "wrapped": wrapped}
        return SyncEnvelope(
            dataset=dataset, service_id=sid, origin_id=self.node_id,
            version=tuple(v), tombstone=False, payload=payload,
        )

    def _build_envelope_for_key(self, key: _Key) -> Optional[SyncEnvelope]:
        ds, origin, sid = key
        if origin == self.node_id:
            return self._build_local_envelope(ds, sid)
        with self._lock:
            return self._foreign.get(key)

    # ── inbound apply (LWW dedup; no relay until M2) ────────────────────

    def apply_inbound(self, env: SyncEnvelope) -> bool:
        """Accept ``env`` into the foreign overlay iff strictly newer than
        what we have. Returns True if accepted (stored), else False.

        Self-origin envelopes are always ignored: our own state (including
        tombstones) is authoritative, so a peer can never reintroduce a
        record we own.
        """
        if env.origin_id == self.node_id:
            return False
        with self._lock:
            cur = self._foreign.get(env.key)
            cur_v = cur.version if cur is not None else None
            if not version_newer(env.version, cur_v):
                return False
            self._foreign[env.key] = env
            return True

    # ── handlers (peer → us) ────────────────────────────────────────────

    def handle_open(self, body: dict) -> dict:
        """Receive an OPEN: authorize per-namespace, record the session,
        return our node id + the accepted namespaces.

        The candidate namespace set is the union of what the caller offered
        (its own datasets) and our own datasets, so both sides' namespaces
        get synced (subject to auth).
        """
        from_node = body["node_id"]
        address = body.get("address", "")
        offered = set(body.get("namespaces") or [])
        token = body.get("token")
        local_ns = set(self._registry.list_datasets()) if self._registry else set()
        candidate = sorted(offered | local_ns)
        accepted, ephemeral = authorize_namespaces(
            self._registry, self._auth_store_getter(), candidate, token,
        )
        with self._lock:
            self._sessions[from_node] = Peer(from_node, address, set(accepted))
        logger.info("cluster: session opened with %s (ns=%s)", from_node, accepted)
        return {"node_id": self.node_id, "accepted": accepted, "ephemeral": ephemeral}

    def _public_namespaces(self) -> Set[str]:
        if self._registry is None:
            return set()
        auth_store = self._auth_store_getter()
        return {
            ds for ds in self._registry.list_datasets()
            if auth_store is None or not self._registry.is_auth_required(ds)
        }

    def _allowed_for(self, from_node: str, requested: Optional[List[str]]) -> Set[str]:
        with self._lock:
            sess = self._sessions.get(from_node)
        base = set(sess.namespaces) if sess is not None else self._public_namespaces()
        if requested:
            base &= set(requested)
        return base

    def serve_digest(self, from_node: str, namespaces: Optional[List[str]]) -> list:
        """Return ``[dataset, origin_id, service_id, version]`` rows for the
        records visible to ``from_node`` (session-scoped)."""
        allowed = self._allowed_for(from_node, namespaces)
        idx = self._full_index(sorted(allowed) if allowed else [])
        return [
            [ds, origin, sid, list(ver)]
            for (ds, origin, sid), ver in idx.items()
            if ds in allowed
        ]

    def serve_pull(self, from_node: str, keys: List[list]) -> list:
        """Return full envelopes for the requested keys (session-scoped)."""
        allowed = self._allowed_for(from_node, None)
        out = []
        for k in keys:
            ds, origin, sid = k[0], k[1], k[2]
            if ds not in allowed:
                continue
            env = self._build_envelope_for_key((ds, origin, sid))
            if env is not None:
                out.append(env.model_dump(mode="json"))
        return out

    def serve_updates(self, from_node: str, envelopes: List[dict]) -> dict:
        """Apply a batch of inbound envelopes (LWW dedup) and relay the
        accepted ones onward with split-horizon (everyone except the sender).

        Loops can't run away: a relayed envelope that a node already has at
        the same version is rejected by ``apply_inbound`` and therefore not
        relayed again, so the flood dies out after each node sees it once.
        """
        accepted = 0
        to_relay: List[SyncEnvelope] = []
        for raw in envelopes:
            env = SyncEnvelope.model_validate(raw)
            if self.apply_inbound(env):
                accepted += 1
                to_relay.append(env)
        for env in to_relay:
            self._broadcast(env, exclude=from_node)
        return {"accepted": accepted, "received": len(envelopes)}

    # ── outbound replication ────────────────────────────────────────────

    def _broadcast(self, env: SyncEnvelope, exclude: Optional[str] = None) -> None:
        """Send ``env`` to every session that syncs its dataset, except
        ``exclude`` (split-horizon). Best-effort: a peer that's unreachable
        is skipped — periodic anti-entropy (M3) will reconcile it."""
        payload = [env.model_dump(mode="json")]
        with self._lock:
            peers = list(self._sessions.values())
        for peer in peers:
            if peer.node_id == exclude or env.dataset not in peer.namespaces:
                continue
            try:
                self._transport.updates(peer.address, self.node_id, payload)
            except TransportError:
                pass

    # ── orchestration (us → peer) ───────────────────────────────────────

    def connect_peer(
        self, address: str, namespaces: Optional[List[str]] = None, token: Optional[str] = None,
    ) -> Peer:
        """Initiate a session with the peer at ``address`` and run an
        initial full reconcile. ``namespaces`` defaults to our own datasets
        so the peer learns everything we host."""
        offered = list(namespaces) if namespaces else (
            list(self._registry.list_datasets()) if self._registry else []
        )
        body = {
            "node_id": self.node_id,
            "address": self._advertise,
            "namespaces": offered,
            "token": token,
        }
        resp = self._transport.open(address, body)
        peer = Peer(resp["node_id"], address, set(resp.get("accepted") or []))
        with self._lock:
            self._sessions[peer.node_id] = peer
        logger.info("cluster: connected to %s (ns=%s)", peer.node_id, sorted(peer.namespaces))
        self.reconcile(peer)
        return peer

    def reconcile(self, peer: Peer) -> dict:
        """Bidirectional full reconcile with ``peer``: pull what it has
        newer/we lack, push what we have newer/it lacks. Best-effort —
        transport errors propagate to the caller."""
        ns = sorted(peer.namespaces)
        remote_rows = self._transport.digest(peer.address, self.node_id, ns)
        remote_index: Dict[_Key, Version] = {
            (r[0], r[1], r[2]): tuple(r[3]) for r in remote_rows
        }
        local_index = self._full_index(ns)

        to_pull = [
            [d, o, s] for (d, o, s), rv in remote_index.items()
            if version_newer(rv, local_index.get((d, o, s)))
        ]
        pulled = 0
        if to_pull:
            for raw in self._transport.pull(peer.address, self.node_id, to_pull):
                if self.apply_inbound(SyncEnvelope.model_validate(raw)):
                    pulled += 1

        push_envs = []
        for key, lv in local_index.items():
            if version_newer(lv, remote_index.get(key)):
                env = self._build_envelope_for_key(key)
                if env is not None:
                    push_envs.append(env.model_dump(mode="json"))
        pushed = 0
        if push_envs:
            res = self._transport.updates(peer.address, self.node_id, push_envs)
            pushed = res.get("accepted", 0)

        logger.info("cluster: reconciled with %s (pulled=%d pushed=%d)",
                    peer.node_id, pulled, pushed)
        return {"pulled": pulled, "pushed": pushed}

    def list_peers(self) -> List[Peer]:
        with self._lock:
            return list(self._sessions.values())

    def gc_tombstones(self, now_ms: Optional[int] = None) -> int:
        """Drop tombstones older than the retention window (``beacon_ttl +
        beacon_grace``) — local (persisted) and foreign (overlay). Returns
        the number removed.

        Retention ≥ the foreign-replica eviction window guarantees any peer
        that could still hold a stale copy has already evicted it before we
        forget the deletion, so GC can't cause a resurrection.
        """
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000
        retention_ms = int(self._config.tombstone_retention * 1000)
        removed = 0
        with self._lock:
            for k, t in list(self._state.tombstones.items()):
                if now_ms - t.deleted_at_ms > retention_ms:
                    del self._state.tombstones[k]
                    removed += 1
            if removed:
                self._state.save()
            for key, env in list(self._foreign.items()):
                if env.tombstone and now_ms - env.version[0] > retention_ms:
                    del self._foreign[key]
                    removed += 1
        return removed

    def disconnect_peer(self, node_id: str) -> bool:
        """Drop the session and evict records that originated at that peer.

        (Records learned transitively *through* this peer but originating
        elsewhere are evicted by beacon-lease expiry in M4.)
        """
        with self._lock:
            existed = self._sessions.pop(node_id, None) is not None
            for k in [k for k in self._foreign if k[1] == node_id]:
                del self._foreign[k]
        return existed

    # ── read seams (dataset router merge calls these; wired in M5) ───────

    def foreign_wrapped(self, dataset: str) -> List[dict]:
        """Wrapped-output dicts for replicated live records in ``dataset``,
        with a namespaced ``id`` (``origin_id:service_id``) + ``origin_id``."""
        out: List[dict] = []
        with self._lock:
            for (ds, origin, sid), env in self._foreign.items():
                if ds != dataset or env.tombstone or not env.payload:
                    continue
                wrapped = env.payload.get("wrapped")
                if not wrapped:
                    continue
                row = dict(wrapped)
                row["id"] = f"{origin}:{sid}"
                row["origin_id"] = origin
                out.append(row)
        return out

    def foreign_entry(self, dataset: str, display_id: str):
        """Resolve a namespaced ``origin_id:service_id`` to its replicated
        wrapped record, or None. Used by the single-get endpoint."""
        if ":" not in display_id:
            return None
        origin, _, sid = display_id.partition(":")
        with self._lock:
            env = self._foreign.get((dataset, origin, sid))
        if env is None or env.tombstone or not env.payload:
            return None
        wrapped = dict(env.payload["wrapped"])
        wrapped["id"] = display_id
        wrapped["origin_id"] = origin
        return wrapped

    # ── local mutation hook (wired via RegistryService.set_on_mutation) ──

    def on_local_mutation(self, dataset: str, service_id: str, op: str, entry) -> None:
        """Called after every successful local CRUD. Stamps a new version on
        the record (a tombstone on deregister), persists, and pushes the
        delta to all peers that sync this namespace.

        origin-only: we only ever stamp records we own, so versions stay
        monotonic per record and there's no write-write conflict.
        """
        with self._lock:
            ts = self._next_ts()
            k = make_key(dataset, service_id)
            if op == "deregister":
                self._state.tombstones[k] = Tombstone(
                    version=(ts, self.node_id), deleted_at_ms=ts,
                )
                self._state.local_versions.pop(k, None)
            else:  # register | update
                self._state.local_versions[k] = [ts, self.node_id]
                self._state.tombstones.pop(k, None)  # un-tombstone on re-register
            self._state.save()

        env = self._build_local_envelope(dataset, service_id)
        if env is not None:
            self._broadcast(env, exclude=None)

    # ── observability ───────────────────────────────────────────────────

    def state_summary(self) -> dict:
        with self._lock:
            foreign_by_ns: Dict[str, int] = {}
            for (ds, _o, _s), env in self._foreign.items():
                if not env.tombstone:
                    foreign_by_ns[ds] = foreign_by_ns.get(ds, 0) + 1
            return {
                "node_id": self.node_id,
                "advertise": self._advertise,
                "peers": [p.to_summary() for p in self._sessions.values()],
                "foreign_records": sum(foreign_by_ns.values()),
                "foreign_by_namespace": foreign_by_ns,
                "local_records": len(self._state.local_versions),
                "tombstones": len(self._state.tombstones),
            }
