"""Synchronous examples for ``A2XRegistryClient.set_status``.

This file demonstrates:

1. setting status to each valid value: 'online' / 'busy' / 'offline'
2. local fail-fast on invalid status (no HTTP)
3. NotOwnedError on a sid this client didn't register
4. NotFoundError when backend returns 404 + automatic ownership cleanup
5. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/set_status_sync.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
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


def make_card(name: str, description: str) -> dict:
    return {"protocolVersion": "0.0", "name": name, "description": description}


def ensure_absent(client: A2XRegistryClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_set_status_sync.json"
    print(f"Using backend: {base_url}")

    with A2XRegistryClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_set_status_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        reg = client.register_agent(
            ds, make_card("StatusDemo", "demo agent"),
            service_id="agent_status_demo",
            persistent=True,
        )
        sid = reg.service_id

        # 1) Each valid status value
        print("\n[set_status — valid values]")
        for status in ["busy", "offline", "online"]:
            r = client.set_status(ds, sid, status)
            print(f"  status={status} → backend status field={r.status} "
                  f"changed_fields={r.changed_fields}")

        # Verify on backend
        detail = client.get_agent(ds, sid)
        print(f"\n  current status on backend: {detail.metadata.get('status')!r}")

        # 2) Invalid status → ValueError, no HTTP
        print("\n[invalid status → ValueError, no HTTP]")
        for bad in ["ONLINE", "available", "", None, 0, True, " online "]:
            try:
                client.set_status(ds, sid, bad)
            except ValueError as exc:
                print(f"  status={bad!r:>12}: {type(exc).__name__}")

        # 3) Foreign sid → NotOwnedError
        print("\n[foreign sid → NotOwnedError, no HTTP]")
        try:
            client.set_status(ds, "never_registered", "online")
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")

        client.delete_dataset(ds)

    # 4) NotFoundError + auto-cleanup
    print("\n[backend deleted under us → NotFoundError + ownership cleanup]")
    with A2XRegistryClient(base_url=base_url, ownership_file=False) as client2:
        client2._owned.add("nonexistent_ds", "nonexistent_sid")
        try:
            client2.set_status("nonexistent_ds", "nonexistent_sid", "busy")
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")
            print(f"  ownership cleared: "
                  f"{not client2._owned.contains('nonexistent_ds', 'nonexistent_sid')}")

    # 5) Network failure
    print("\n[network / gateway failure]")
    try:
        with A2XRegistryClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            bad_client.set_status("ds", "sid", "online")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")


if __name__ == "__main__":
    main()
