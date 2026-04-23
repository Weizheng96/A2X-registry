"""Synchronous examples for ``A2XRegistryClient.restore_to_blank``.

This file demonstrates:

1. round trip: blank → team → restore back to blank (L1 cache hit, no GET)
2. L2 fallback: when L1 cache is cold, SDK reads endpoint via get_agent
3. L3: ValueError when no endpoint anywhere (cache cleared + card lacks endpoint)
4. NotOwnedError on foreign sid
5. NotFoundError if sid was deleted on backend
6. network failure

Run:
    python examples/client_sdk/restore_to_blank_sync.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_client import (
    A2XRegistryClient,
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)


def ensure_absent(client: A2XRegistryClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_restore_to_blank_sync.json"

    with A2XRegistryClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_restore_to_blank_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        # 1) Full lifecycle: blank → team → blank
        print("\n[lifecycle: blank → team → blank]")
        reg = client.register_blank_agent(ds, endpoint="http://teammate:8080")
        sid = reg.service_id

        # Take it out of the idle pool
        client.replace_agent_card(ds, sid, {
            "name": "Worker", "description": "已组队",
            "endpoint": "http://teammate:8080", "status": "busy",
        })
        idle_when_busy = client.list_idle_blank_agents(ds, n=10)
        print(f"  idle pool while teamed: {len(idle_when_busy)}")

        # Restore — L1 cache primed by earlier replace, so 0 extra GET
        print("\n[restore — L1 cache hit, no GET]")
        client.restore_to_blank(ds, sid)
        idle_after = client.list_idle_blank_agents(ds, n=10)
        print(f"  idle pool after restore: {len(idle_after)}")
        # Verify card is blank
        d = client.get_agent(ds, sid)
        print(f"  description: {d.metadata['description']!r}")
        print(f"  status: {d.metadata['status']}")

        # 2) L2 fallback: clear L1 cache, restore reads endpoint via get_agent
        print("\n[L2 fallback — clear L1, restore reads endpoint via GET]")
        # First, set up a non-blank state again
        client.replace_agent_card(ds, sid, {
            "name": "Worker", "description": "组队中",
            "endpoint": "http://teammate:8080", "status": "busy",
        })
        client._blank_endpoints.clear()  # simulate process restart
        client.restore_to_blank(ds, sid)
        d = client.get_agent(ds, sid)
        print(f"  description after L2 restore: {d.metadata['description']!r}")

        # 3) L3: card without endpoint → ValueError
        print("\n[L3 — card lacks endpoint, restore can't resolve]")
        # Bypass auto-fill by using update_agent (PUT) to replace endpoint with empty
        # Actually easier: register a fresh sid via register_agent with no endpoint
        bare_card = {"protocolVersion": "0.0", "name": "BareAgent",
                     "description": "no endpoint here"}
        bare = client.register_agent(ds, bare_card, service_id="agent_no_endpoint")
        client._blank_endpoints.clear()
        try:
            client.restore_to_blank(ds, bare.service_id)
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        # 4) Foreign sid → NotOwnedError, no HTTP
        print("\n[foreign sid → NotOwnedError, no HTTP]")
        try:
            client.restore_to_blank(ds, "never_owned")
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")

        # 5) NotFoundError if backend deleted the sid
        print("\n[backend deleted → NotFoundError]")
        with A2XRegistryClient(base_url=base_url, ownership_file=False) as other:
            other._owned.add(ds, sid)
            other.deregister_agent(ds, sid)
        try:
            client.restore_to_blank(ds, sid)
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")

        client.delete_dataset(ds)

    # 6) Network failure
    print("\n[network failure]")
    try:
        with A2XRegistryClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            bad_client._blank_endpoints[("ds", "sid")] = "http://x"
            bad_client.restore_to_blank("ds", "sid")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    main()
