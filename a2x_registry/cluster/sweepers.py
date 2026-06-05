"""Background daemons for the cluster module.

``AntiEntropySweeper`` periodically reconciles with each peer (healing any
push that was dropped while a link was flaky) and GCs expired tombstones.
Mirrors ``HeartbeatSweeper``'s defensive structure: a single daemon thread,
each tick wrapped so one error never kills the loop.

``tick()`` is exposed so tests drive it synchronously (no sleep).
"""

from __future__ import annotations

import logging
import threading

from .transport import TransportError

logger = logging.getLogger(__name__)


class AntiEntropySweeper:
    """Periodic reconcile + tombstone GC."""

    def __init__(self, store, period: float = 20.0) -> None:
        self._store = store
        self._period = float(period)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """One pass: reconcile each peer (best-effort) then GC tombstones."""
        for peer in self._store.list_peers():
            try:
                self._store.reconcile(peer)
            except TransportError:
                pass  # peer unreachable now; a later tick will catch up
            except Exception as exc:  # noqa: BLE001 — never kill the loop
                logger.warning("cluster: anti-entropy reconcile with %s failed: %s",
                               peer.node_id, exc)
        self._store.gc_tombstones()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ClusterAntiEntropy", daemon=True,
        )
        self._thread.start()
        logger.info("cluster: anti-entropy sweeper started (period=%ss)", self._period)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.exception("cluster: anti-entropy tick raised: %s", exc)
            self._stop.wait(self._period)
