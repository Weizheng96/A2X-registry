"""Synchronous examples for ``A2XClient.list_agents``.

This file demonstrates:

1. successful listing after registering multiple agents
2. empty dataset returns ``[]``
3. nonexistent dataset also returns ``[]``
4. network / gateway failure (backend unreachable)

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


def make_card(name: str, description: str) -> dict[str, str]:
    return {
        "protocolVersion": "0.0",
        "name": name,
        "description": description,
    }


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Using backend: {base_url}")

    with A2XClient(base_url=base_url, ownership_file=False) as client:
        ds_full = "example_list_agents_sync_full"
        ds_empty = "example_list_agents_sync_empty"

        ensure_absent(client, ds_full)
        ensure_absent(client, ds_empty)

        client.create_dataset(ds_full)
        client.create_dataset(ds_empty)

        client.register_agent(
            ds_full,
            make_card("Planner Sync", "拆解问题并给出执行计划"),
            service_id="agent_list_sync_planner",
            persistent=True,
        )
        client.register_agent(
            ds_full,
            make_card("Researcher Sync", "检索资料并整理信息"),
            service_id="agent_list_sync_researcher",
            persistent=True,
        )

        print("\n[successful listing]")
        agents = client.list_agents(ds_full)
        print(f"  count: {len(agents)}")
        for brief in agents:
            print(f"  - {brief.id}: {brief.name} | {brief.description}")

        print("\n[empty dataset -> []]")
        empty_agents = client.list_agents(ds_empty)
        print(f"  count: {len(empty_agents)}")
        print(f"  payload: {empty_agents}")

        print("\n[nonexistent dataset -> []]")
        missing_agents = client.list_agents("example_list_agents_sync_missing")
        print(f"  count: {len(missing_agents)}")
        print(f"  payload: {missing_agents}")

        client.delete_dataset(ds_full)
        client.delete_dataset(ds_empty)

    print("\n[network / gateway failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999", ownership_file=False) as bad_client:
            bad_client.list_agents("example_list_agents_sync_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  message: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  status_code: {exc.status_code}")
        print(f"  message: {exc}")
        print(f"  payload: {exc.payload}")


if __name__ == "__main__":
    main()
