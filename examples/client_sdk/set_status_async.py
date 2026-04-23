"""Asynchronous examples for ``AsyncA2XClient.set_status``.

Mirrors ``set_status_sync.py`` covering all the same paths with `await`.
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

from a2x_client import (
    AsyncA2XClient,
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)


def make_card(name: str, description: str) -> dict:
    return {"protocolVersion": "0.0", "name": name, "description": description}


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_set_status_async.json"

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_set_status_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        reg = await client.register_agent(
            ds, make_card("StatusDemoAsync", "demo agent"),
            service_id="agent_status_demo_async",
            persistent=True,
        )
        sid = reg.service_id

        print("\n[set_status — valid values]")
        for status in ["busy", "offline", "online"]:
            r = await client.set_status(ds, sid, status)
            print(f"  status={status} → backend status={r.status}")

        detail = await client.get_agent(ds, sid)
        print(f"\n  current status on backend: {detail.metadata.get('status')!r}")

        print("\n[invalid status → ValueError]")
        for bad in ["ONLINE", "", None, 0]:
            try:
                await client.set_status(ds, sid, bad)
            except ValueError as exc:
                print(f"  status={bad!r:>12}: {type(exc).__name__}")

        print("\n[foreign sid → NotOwnedError]")
        try:
            await client.set_status(ds, "never_registered", "online")
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")

        await client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            await bad_client.set_status("ds", "sid", "online")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
