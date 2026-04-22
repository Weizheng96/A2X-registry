"""Synchronous examples for ``A2XClient.list_agents``.

This file demonstrates:

1. listing all services (no filters → GET /services with no query params)
2. filtering by a single field (description)
3. composite AND filters (description + status)
4. flat return shape: list[dict] with id + raw card fields merged
5. local fail-fast on reserved filter keys / None values
6. empty/nonexistent dataset returns []
7. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/list_agents_sync.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import A2XClient, A2XConnectionError, A2XHTTPError, ValidationError


def make_card(name: str, description: str, **extra) -> dict:
    return {"protocolVersion": "0.0", "name": name, "description": description, **extra}


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Using backend: {base_url}")

    with A2XClient(base_url=base_url, ownership_file=False) as client:
        ds = "example_list_agents_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        # Seed: 1 a2a (idle), 1 a2a (working), 1 generic
        client.register_agent(
            ds,
            make_card("planner", "拆解任务", endpoint="http://a", status="online"),
            service_id="agent_planner",
        )
        client.register_agent(
            ds,
            make_card("worker", "执行子任务", endpoint="http://b", status="busy"),
            service_id="agent_worker",
        )
        # Note: register a generic via the same endpoint; for example purposes
        # we just register another a2a with a distinctive description.
        client.register_agent(
            ds,
            make_card("scribe", "记录笔记", endpoint="http://c", status="online"),
            service_id="agent_scribe",
        )

        # 1) No filters → all services
        print("\n[no filters → all services]")
        all_agents = client.list_agents(ds)
        print(f"  count: {len(all_agents)}")
        for a in all_agents:
            print(f"    id={a['id']} name={a['name']!r} "
                  f"endpoint={a.get('endpoint')!r} status={a.get('status', 'online')}")

        # 2) Single filter: status='online' (only idle)
        print("\n[filter: status=online]")
        idle = client.list_agents(ds, status="online")
        print(f"  count: {len(idle)}, ids: {[a['id'] for a in idle]}")

        # 3) Composite AND filter
        print("\n[composite filter: name='planner' AND status=online]")
        focused = client.list_agents(ds, name="planner", status="online")
        print(f"  count: {len(focused)}, ids: {[a['id'] for a in focused]}")

        # 4) Filter that matches nothing
        print("\n[filter that matches nothing]")
        empty = client.list_agents(ds, name="nonexistent_name")
        print(f"  count: {len(empty)}")

        # 5) Reserved filter key → local ValueError, no HTTP
        print("\n[reserved filter key → ValueError (no HTTP)]")
        try:
            client.list_agents(ds, mode="bogus")
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        # 6) None filter value → local ValueError
        print("\n[None filter value → ValueError]")
        try:
            client.list_agents(ds, name=None)
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        # 7) Empty filter key → local ValueError
        print("\n[empty filter key → ValueError]")
        try:
            client.list_agents(ds, **{"": "x"})
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        # 8) Empty / nonexistent dataset returns []
        print("\n[empty dataset → []]")
        empty_ds = "example_list_agents_sync_empty"
        ensure_absent(client, empty_ds)
        client.create_dataset(empty_ds)
        print(f"  empty dataset count: {len(client.list_agents(empty_ds))}")
        print(f"  nonexistent dataset count: "
              f"{len(client.list_agents('example_list_agents_sync_missing'))}")

        client.delete_dataset(ds)
        client.delete_dataset(empty_ds)

    # 9) Network / gateway failure
    print("\n[network / gateway failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client.list_agents("example_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  message: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  status_code: {exc.status_code}")


if __name__ == "__main__":
    main()
