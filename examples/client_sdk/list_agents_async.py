"""Asynchronous examples for ``AsyncA2XClient.list_agents``.

Mirrors ``list_agents_sync.py``; same coverage, async style.

Run:
    python examples/client_sdk/list_agents_async.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_client import (
    AsyncA2XClient,
    A2XConnectionError,
    A2XHTTPError,
    ValidationError,
)


def make_card(name: str, description: str, **extra) -> dict:
    return {"protocolVersion": "0.0", "name": name, "description": description, **extra}


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Using backend: {base_url}")

    async with AsyncA2XClient(base_url=base_url, ownership_file=False) as client:
        ds = "example_list_agents_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        await asyncio.gather(
            client.register_agent(
                ds,
                make_card("planner", "拆解任务", endpoint="http://a", status="online"),
                service_id="agent_planner",
            ),
            client.register_agent(
                ds,
                make_card("worker", "执行子任务", endpoint="http://b", status="busy"),
                service_id="agent_worker",
            ),
            client.register_agent(
                ds,
                make_card("scribe", "记录笔记", endpoint="http://c", status="online"),
                service_id="agent_scribe",
            ),
        )

        print("\n[no filters → all services]")
        all_agents = await client.list_agents(ds)
        for a in all_agents:
            print(f"    id={a['id']} name={a['name']!r} status={a.get('status', 'online')}")

        print("\n[filter: status=online]")
        idle = await client.list_agents(ds, status="online")
        print(f"  ids: {[a['id'] for a in idle]}")

        print("\n[composite: name='planner' AND status=online]")
        focused = await client.list_agents(ds, name="planner", status="online")
        print(f"  ids: {[a['id'] for a in focused]}")

        print("\n[reserved filter key → ValueError (no HTTP)]")
        try:
            await client.list_agents(ds, mode="bogus")
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        print("\n[None filter value → ValueError]")
        try:
            await client.list_agents(ds, name=None)
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}: {str(exc)[:60]}...")

        print("\n[nonexistent dataset → []]")
        print(f"  count: {len(await client.list_agents('example_list_agents_async_missing'))}")

        await client.delete_dataset(ds)

    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            await bad_client.list_agents("example_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
