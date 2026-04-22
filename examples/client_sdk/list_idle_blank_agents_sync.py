"""Synchronous examples for ``A2XClient.list_idle_blank_agents``.

This file demonstrates:

1. default n=1 returns at most 1 blank agent
2. explicit n returns up to N
3. backend filter is description='__BLANK__' AND status="online" (strict idle)
4. flat return shape: id + raw card fields
5. n=0 short-circuits with no HTTP
6. invalid n (-1, 1.5, "3", None) → ValueError, no HTTP
7. empty pool → []
8. network failure

Run:
    python examples/client_sdk/list_idle_blank_agents_sync.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import A2XClient, A2XConnectionError, A2XHTTPError, ValidationError


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_list_idle_sync.json"

    with A2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_list_idle_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        # Seed: 3 blank agents (all idle), 1 non-blank (working teammate)
        for i in range(3):
            client.register_blank_agent(ds, endpoint=f"http://idle-{i}:8080")
        # Register a non-blank a2a (will not match the idle filter)
        client.register_agent(
            ds,
            {"protocolVersion": "0.0", "name": "TeamLead",
             "description": "已组队的负责人",
             "endpoint": "http://lead:8080", "status": "busy"},
            service_id="agent_lead",
        )

        # 1) Default n=1
        print("\n[default n=1]")
        idle = client.list_idle_blank_agents(ds)
        print(f"  count: {len(idle)}")
        if idle:
            print(f"  picked: id={idle[0]['id']} endpoint={idle[0]['endpoint']!r}")

        # 2) Explicit n=5 — should get all 3 idle ones
        print("\n[explicit n=5]")
        idle_all = client.list_idle_blank_agents(ds, n=5)
        print(f"  count: {len(idle_all)}")
        for a in idle_all:
            print(f"    id={a['id']} endpoint={a['endpoint']} status={a['status']!r}")

        # 3) Verify the non-blank lead is excluded
        all_agents = client.list_agents(ds)
        print(f"\n  total in dataset: {len(all_agents)}")
        print(f"  idle (filtered): {len(idle_all)}")
        assert len(all_agents) > len(idle_all)

        # 4) n=0 short-circuit, no HTTP
        print("\n[n=0 → empty, no HTTP]")
        print(f"  result: {client.list_idle_blank_agents(ds, n=0)}")

        # 5) Invalid n → ValueError
        print("\n[invalid n → ValueError, no HTTP]")
        for bad in [-1, 1.5, "3", None, True]:
            try:
                client.list_idle_blank_agents(ds, n=bad)
            except ValueError as exc:
                print(f"  n={bad!r:>10}: {type(exc).__name__}")

        # 6) After all idle agents are taken (count > 0), pool is empty
        print("\n[empty idle pool]")
        # Take all idle agents by replacing their cards with non-blank state
        for a in idle_all:
            client.replace_agent_card(ds, a["id"], {
                "name": "Working", "description": "busy now",
                "endpoint": a["endpoint"], "status": "busy",
            })
        empty = client.list_idle_blank_agents(ds, n=10)
        print(f"  count after all teamed up: {len(empty)}")

        client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client.list_idle_blank_agents("example_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    main()
