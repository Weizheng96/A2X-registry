"""Asynchronous examples for ``AsyncA2XClient.list_agents``.

This file demonstrates:

1. successful listing after registering multiple agents
2. empty dataset returns ``[]``
3. nonexistent dataset also returns ``[]``
4. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/list_agents_async.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import (
    A2XConnectionError,
    A2XHTTPError,
    AsyncA2XClient,
    ValidationError,
)


def make_card(name: str, description: str) -> dict[str, str]:
    return {
        "protocolVersion": "0.0",
        "name": name,
        "description": description,
    }


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Using backend: {base_url}")

    async with AsyncA2XClient(base_url=base_url, ownership_file=False) as client:
        ds_full = "example_list_agents_async_full"
        ds_empty = "example_list_agents_async_empty"

        await ensure_absent(client, ds_full)
        await ensure_absent(client, ds_empty)

        await client.create_dataset(ds_full)
        await client.create_dataset(ds_empty)

        await client.register_agent(
            ds_full,
            make_card("Planner Async", "拆解问题并给出执行计划"),
            service_id="agent_list_async_planner",
            persistent=True,
        )
        await client.register_agent(
            ds_full,
            make_card("Researcher Async", "检索资料并整理信息"),
            service_id="agent_list_async_researcher",
            persistent=True,
        )

        print("\n[successful listing]")
        agents = await client.list_agents(ds_full)
        print(f"  count: {len(agents)}")
        for brief in agents:
            print(f"  - {brief.id}: {brief.name} | {brief.description}")

        print("\n[empty dataset -> []]")
        empty_agents = await client.list_agents(ds_empty)
        print(f"  count: {len(empty_agents)}")
        print(f"  payload: {empty_agents}")

        print("\n[nonexistent dataset -> []]")
        missing_agents = await client.list_agents("example_list_agents_async_missing")
        print(f"  count: {len(missing_agents)}")
        print(f"  payload: {missing_agents}")

        await client.delete_dataset(ds_full)
        await client.delete_dataset(ds_empty)

    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(
            base_url="http://127.0.0.1:8999",
            ownership_file=False,
        ) as bad_client:
            await bad_client.list_agents("example_list_agents_async_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  message: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  status_code: {exc.status_code}")
        print(f"  message: {exc}")
        print(f"  payload: {exc.payload}")


if __name__ == "__main__":
    asyncio.run(main())
