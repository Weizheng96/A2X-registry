"""Peer transport — the RPC boundary between registry instances.

``Transport`` is a small interface with one method per cluster RPC. Two
implementations:

  - ``HttpTransport`` — production; calls the peer's ``/api/cluster/*``
    endpoints over HTTP (``trust_env=False`` so a system proxy can't
    intercept localhost — see CLAUDE.md gotcha).
  - tests provide an in-process transport that routes calls straight to
    the target ``ClusterStore``'s handler methods, so two instances can be
    exercised in one process without real servers or the module singleton.

Every method takes the peer's base ``address`` (e.g. ``http://host:port``)
and returns parsed JSON. Transport errors propagate as ``TransportError``
so the caller can treat an unreachable peer as best-effort.
"""

from __future__ import annotations

from typing import Any, List, Optional

# Header carrying the per-session secret on every authenticated RPC.
SESSION_HEADER = "X-Cluster-Session"


class TransportError(Exception):
    """Raised when a peer call fails (unreachable / non-2xx)."""


class Transport:
    """Interface — see module docstring. ``token`` is the per-session secret
    (None on no-auth clusters)."""

    def open(self, address: str, body: dict) -> dict:
        raise NotImplementedError

    def digest(self, address: str, from_node: str, namespaces: List[str],
               token: Optional[str] = None) -> list:
        raise NotImplementedError

    def pull(self, address: str, from_node: str, keys: List[list],
             token: Optional[str] = None) -> list:
        raise NotImplementedError

    def updates(self, address: str, from_node: str, envelopes: List[dict],
                token: Optional[str] = None) -> dict:
        raise NotImplementedError

    def keepalive(self, address: str, from_node: str,
                  token: Optional[str] = None) -> dict:
        raise NotImplementedError


class HttpTransport(Transport):
    """Production transport over the peer's REST endpoints."""

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    def _call(self, address: str, method: str, path: str,
              token: Optional[str] = None, **kw) -> Any:
        import httpx

        url = address.rstrip("/") + path
        if token is not None:
            kw.setdefault("headers", {})[SESSION_HEADER] = token
        try:
            # trust_env=False → ignore system proxies (localhost interception).
            with httpx.Client(trust_env=False, timeout=self._timeout) as client:
                resp = client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise TransportError(f"{method} {url} failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise TransportError(f"{method} {url} → {resp.status_code}: {resp.text}")
        return resp.json()

    def open(self, address: str, body: dict) -> dict:
        return self._call(address, "POST", "/api/cluster/sessions", json=body)

    def digest(self, address, from_node, namespaces, token=None) -> list:
        return self._call(
            address, "GET", "/api/cluster/digest", token=token,
            params={"from_node": from_node, "namespaces": ",".join(namespaces)},
        )

    def pull(self, address, from_node, keys, token=None) -> list:
        return self._call(
            address, "POST", "/api/cluster/pulls", token=token,
            json={"from_node": from_node, "keys": keys},
        )

    def updates(self, address, from_node, envelopes, token=None) -> dict:
        return self._call(
            address, "POST", "/api/cluster/updates", token=token,
            json={"from_node": from_node, "envelopes": envelopes},
        )

    def keepalive(self, address, from_node, token=None) -> dict:
        return self._call(
            address, "POST", "/api/cluster/keepalives", token=token,
            json={"from_node": from_node},
        )
