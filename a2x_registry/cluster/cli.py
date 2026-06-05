"""``a2x-registry cluster ...`` CLI.

The primary, user-facing way to drive distributed sync. Dispatched from
``backend/__main__.py`` when argv starts with ``cluster``.

Commands (M0):
  init     — generate this instance's node id + cluster_state.json (opt-in
             switch; the running server picks it up on next start).
  status   — pretty-print GET /api/cluster/state from a running server.

``add-peer`` / ``rm-peer`` arrive with the session milestone. ``status``
talks to the local server over HTTP (cross-platform; no OS-specific IPC).
"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional

from .state import ClusterState, state_path

DEFAULT_SERVER = "http://127.0.0.1:8000"


def cmd_init(args: argparse.Namespace) -> int:
    try:
        state = ClusterState.init(node_id=args.node_id)
    except FileExistsError as exc:
        print(f"error: {exc}")
        return 1
    print(f"Cluster initialized.")
    print(f"  node_id : {state.node_id}")
    print(f"  state   : {state.path}")
    print("Restart the registry server for the cluster module to load.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    import httpx

    url = args.server.rstrip("/") + "/api/cluster/state"
    try:
        # trust_env=False → ignore system proxies so localhost isn't
        # intercepted (Clash/VPN on Windows) — see CLAUDE.md gotcha.
        with httpx.Client(trust_env=False, timeout=5.0) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        print(f"error: cannot reach server at {args.server}: {exc}")
        return 1
    if resp.status_code == 404:
        print("Cluster module not initialized on the server "
              "(run 'a2x-registry cluster init', then restart the server).")
        return 1
    if resp.status_code != 200:
        print(f"error: server returned {resp.status_code}: {resp.text}")
        return 1
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a2x-registry cluster")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Generate node id + cluster_state.json")
    p_init.add_argument("--node-id", default=None,
                        help="Explicit node id (default: auto-generated UUID)")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="Show sync state from a running server")
    p_status.add_argument("--server", default=DEFAULT_SERVER,
                          help=f"Registry base URL (default: {DEFAULT_SERVER})")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point invoked from ``a2x-registry cluster ...`` dispatcher."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
