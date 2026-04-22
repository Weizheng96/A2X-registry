"""Asynchronous examples for ``AsyncA2XClient.list_idle_blank_agents``.

Mirrors ``list_idle_blank_agents_sync.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import AsyncA2XClient, A2XConnectionError, A2XHTTPError, ValidationError


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_list_idle_async.json"

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_list_idle_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        await asyncio.gather(
            client.register_blank_agent(ds, endpoint=f"http://idle-0:8080"),
            client.register_blank_agent(ds, endpoint=f"http://idle-1:8080"),
            client.register_blank_agent(ds, endpoint=f"http://idle-2:8080"),
            client.register_agent(
                ds,
                {"protocolVersion": "0.0", "name": "TeamLead", "description": "已组队",
                 "endpoint": "http://lead:8080", "status": "busy"},
                service_id="agent_lead",
            ),
        )

        print("\n[default n=1]")
        idle = await client.list_idle_blank_agents(ds)
        print(f"  count: {len(idle)}")

        print("\n[explicit n=5]")
        idle_all = await client.list_idle_blank_agents(ds, n=5)
        print(f"  count: {len(idle_all)}")
        for a in idle_all:
            print(f"    id={a['id']} endpoint={a['endpoint']}")

        print("\n[n=0 → empty, no HTTP]")
        print(f"  result: {await client.list_idle_blank_agents(ds, n=0)}")

        print("\n[invalid n → ValueError]")
        for bad in [-1, 1.5, "3", None]:
            try:
                await client.list_idle_blank_agents(ds, n=bad)
            except ValueError as exc:
                print(f"  n={bad!r:>10}: {type(exc).__name__}")

        await client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            await bad_client.list_idle_blank_agents("example_unreachable")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
